# Default VS Code workspace health gate

The P1 VS Code health gate is `bash scripts/android-dev-workspace-compose-smoke.sh`.
It writes `docs/test/dev-workspace-compose-latest.json` and exits zero only when
all of the following evidence is true for the default workspace:

- UI-triggered compose/build/run produced a running `skydnir-dev` container.
- `/containers/json?all=1` and `/containers/<id>/json` agree on the current
  Engine container ID, name, and running state for `skydnir-dev`.
- Android localhost port `18080` accepts a connection.
- `http://127.0.0.1:18080/` returns an HTTP 2xx/3xx code-server response.
- Required VS Code extensions are present when configured; by default the gate
  requires `Continue.continue`, `OpenAI.chatgpt`, and
  `Anthropic.claude-code` from `code-server --list-extensions`.
- The rendered project-library service card export is current, not stale,
  unknown, or ambiguous, and its Engine container ID matches `skydnir-dev`.

Configured ports, completed jobs, stale `state.json`, or extension install
intent are not success by themselves. The JSON artifact keeps each check under
`checks` so CI and manual device runs can identify which proof is missing.

Host-side promotion verification is available with:

```sh
python3 scripts/verify-dev-workspace-compose-artifact.py docs/test/dev-workspace-compose-latest.json
```

The verifier exits zero only for `status=pass` / `success=true` artifacts whose
`build_run`, `engine_state`, `listener`, `code_server_http`, configured
`extensions`, and `ui_truth` checks are all `ok`. `planned-gap`, `fail`,
`blocked`, and other non-pass artifacts are non-promoting and must keep
`success=false`.

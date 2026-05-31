# TermPort Docker API Integration Request

Date: 2026-05-31

## Goal

TermPort should connect to Skydnir container consoles through Docker Engine-compatible APIs only.
The same TermPort implementation must also work against a real Docker daemon exposed over TCP.

This means TermPort must not depend on Skydnir-only Android intents, private activities, private services, app-internal Unix sockets, or Skydnir-specific bridge protocols.
Skydnir should look like a Docker Engine API endpoint from TermPort's point of view.

## Required External API Surface

Expose a TCP endpoint reachable from another Android app on the same device, for example:

- `127.0.0.1:2375`
- or another documented `host:port` configurable by the user

TermPort will use plain Docker-compatible HTTP over TCP. TLS/auth can be added later if needed, but the compatibility contract should stay Docker-shaped.

Required endpoints:

- `GET /_ping`
- `GET /containers/json?all=0`
- `POST /containers/{id}/exec`
- `POST /exec/{id}/start`
- `POST /exec/{id}/resize?h={rows}&w={cols}`

Optional but useful:

- `GET /containers/{id}/json`
- `GET /version`

## Exec Contract

TermPort will create an interactive shell with this Docker-compatible payload:

```json
{
  "AttachStdin": true,
  "AttachStdout": true,
  "AttachStderr": true,
  "Tty": true,
  "Env": [
    "TERM=xterm-256color",
    "COLORTERM=truecolor",
    "ENV=",
    "BASH_ENV="
  ],
  "Cmd": [
    "/bin/sh",
    "-lc",
    "if command -v /bin/bash >/dev/null 2>&1; then exec /bin/bash -i; else exec /bin/sh -i; fi"
  ]
}
```

`POST /exec/{id}/start` should accept:

```json
{
  "Detach": false,
  "Tty": true
}
```

The response must hijack/upgrade the HTTP connection like Docker does:

```http
HTTP/1.1 101 UPGRADED
Connection: Upgrade
Upgrade: tcp
```

After that, the socket should behave as the raw terminal byte stream for stdin/stdout.
With `Tty=true`, TermPort expects raw PTY bytes, not Docker multiplex framing.

## Resize Contract

TermPort will call:

```http
POST /exec/{id}/resize?h={rows}&w={cols}
```

Skydnir should apply the size to the PTY and send `SIGWINCH` or equivalent behavior so full-screen terminal apps redraw correctly.

## Container List Contract

`GET /containers/json?all=0` should return Docker-compatible container objects including at least:

- `Id`
- `Names`
- `Image`
- `State`
- `Status`

TermPort will render shortcuts from that list. Pressing a shortcut connects the active TermPort pane to that container via the exec flow above.

## Non-Goals

TermPort should not require:

- Skydnir-specific Android exported activities
- Skydnir-specific Binder services
- direct access to `filesDir/pdocker/pdockerd.sock`
- an in-container helper unique to Skydnir
- a different terminal protocol per backend

## Validation Targets

A working Skydnir endpoint should pass these TermPort-side checks:

1. `GET /_ping` returns 2xx.
2. `GET /containers/json?all=0` returns running containers.
3. TermPort can create an exec session for a selected container.
4. `POST /exec/{id}/start` upgrades the socket and opens an interactive shell.
5. Text input, Ctrl-C, arrows, and Enter reach the shell correctly.
6. `top`, `vi`, and shell line editing redraw correctly after resize.
7. The same TermPort Docker API path still works against a real Docker daemon exposed over TCP.

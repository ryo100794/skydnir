# Default development workspace

Snapshot date: 2026-05-04.

## Purpose

Skydnir ships a default project template under
`app/src/main/assets/default-project/`. On first app launch it is copied to:

```text
filesDir/pdocker/projects/default/
```

Existing user-edited files are not overwritten.
Existing generated templates are migrated from the old `8080`/`8081` service
ports to `18080`/`18081` when the app starts or a template is installed.

## Scope

This page owns the default workspace operator flow and template contents. For
current device proof, use [`../plan/STATUS.md`](../plan/STATUS.md). For
repeatable compatibility checks, use
[`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md). For public wording about
the workspace, use [`PROMOTION.md`](PROMOTION.md).

## Included Container Stack

The default Dockerfile builds `skydnir/dev-workspace:latest` from Ubuntu 22.04
and installs:

- code-server, used as a VS Code-compatible browser IDE server.
- Continue VS Code extension (`Continue.continue`).
- OpenAI Codex CLI (`npm install -g @openai/codex`).
- OpenAI Codex VS Code extension (`OpenAI.chatgpt`; the Marketplace ID keeps
  the historical `chatgpt` suffix).
- Workspace tasks `Codex: start` and `Codex: version`, so Codex is available
  from `Tasks: Run Task` even when the side panel is hidden by code-server.
- Common development tools: git, Python, Node/npm, ripgrep, jq, SSH client,
  vim, nano, Vulkan tools, and shell utilities.
- Common IDE extensions for Python, YAML, Docker, formatting, linting, Jupyter,
  and Git inspection. Extension install failures are non-fatal because
  Open VSX / marketplace availability can vary by architecture and date.

## Files

```text
default-project/
├── Dockerfile
├── compose.yaml
├── README.md
├── continue/config.yaml
├── documents/README.md
├── scripts/start-code-server.sh
├── workspace/.vscode/extensions.json
├── workspace/.vscode/tasks.json
└── vscode/settings.json
```

## Runtime Notes

- `compose.yaml` starts code-server on `0.0.0.0:18080`, offset from common
  Android/Termux development ports.
- The compose header includes `# skydnir.service-url: 18080=VS Code`. Skydnir
  reads this comment as UI metadata and uses it to label the browser shortcut;
  the Compose service definition remains standard.
- The header also includes `# skydnir.auto-open: VS Code`, which asks Skydnir to
  open that declared service after compose up reports a healthy listener.
- `compose.yaml` requests `gpus: all`, which maps to Skydnir's experimental
  Vulkan passthrough / CUDA-compatible API negotiation.
- The selected Android Documents folder is the default Skydnir workspace root
  only when Android exposes it as a direct app-writable path. The app records
  the persisted SAF tree URI separately and classifies storage as
  `direct-path-writable` or `saf-mediated`; removable SD-card trees that only
  allow URI writes are not advertised to containers as writable POSIX paths.
- In `saf-mediated` mode, Skydnir keeps project definitions and the `/documents`
  exchange surface in an app-private mirror, persists the SAF tree URI, and uses
  a lightweight Android mediator for DocumentProvider directory creation,
  listing, existence checks, and payload reads/writes. The mirror path is what
  Compose binds into containers; the selected SAF tree remains a mediated
  exchange endpoint rather than a fake `/storage/...` bind. Files written under
  `/documents/skydnir-exports/` are buffered through the mirror and then evicted
  from app-private payload storage after the mediator successfully writes them
  to the selected Documents tree; the app keeps sidecar metadata for Unix-like
  mode, timestamp, MIME type, and payload state.
- Removable SD-card Documents trees may be FAT32 or exFAT. Skydnir treats them
  as raw file-payload exchange storage plus app-private metadata for
  Docker-like mode/uid/gid, symlink, and xattr evidence where that evidence can
  be represented. Metadata can be rebuilt or checked from the SAF tree, but
  conflicts with edits from Android file managers must be surfaced instead of
  resolved silently.
- The template mounts the selected Documents folder at `/documents` and the
  cross-project shared folder at `/shared`. Override `SKYDNIR_DOCUMENTS_HOST`
  or `SKYDNIR_SHARED_DOCUMENTS_HOST` when two projects intentionally need the
  same folder; the older `PDOCKER_*` names remain accepted for existing
  workspaces.
- Hot editor state, build caches, databases, and high-frequency logs should use
  app-private fast storage such as `/workspace`; copy selected artifacts to
  `/documents` when they need to be shared.
- `OPENAI_API_KEY`, `GITHUB_TOKEN`, and `CODE_SERVER_PASSWORD` are passed
  through from the environment when provided.
- If `CODE_SERVER_PASSWORD` is empty, code-server starts with `--auth none` for
  local-only development convenience. Set `CODE_SERVER_PASSWORD` before exposing
  the service outside the device.
- Skydnir's current networking model is host-style. Container cards expose
  local browser URLs from Compose ports and `skydnir.service-url` header
  comments.

## Canonical Sources

- Codex CLI install path: `npm install -g @openai/codex`.
- Codex VS Code extension ID: `OpenAI.chatgpt`.
- code-server supports command-line extension installation with
  `code-server --install-extension <extension id>`.
- Continue extension marketplace ID: `Continue.continue`.

## Maintenance

- Keep paths and ports aligned with `app/src/main/assets/default-project/`.
- Link to status and compatibility docs for proof instead of adding new status
  tables here.
- Keep security caveats near the relevant runtime setting.

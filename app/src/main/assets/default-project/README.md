# pdocker default dev workspace

This is the `dev-workspace` container template in the pdocker project library.
It is included in the APK's release assets as the default pdocker management
workspace for project creation, project maintenance, Dockerfile builds, Compose
runs, Engine socket helpers, code-server, Continue, OpenAI Codex/ChatGPT, and
Claude Code.

It includes:

- code-server for browser-based VS Code sessions.
- Continue VS Code extension.
- OpenAI Codex CLI through `npm install -g @openai/codex`.
- OpenAI Codex VS Code extension (`OpenAI.chatgpt`; the Marketplace ID keeps
  the historical `chatgpt` suffix).
- Claude Code CLI through `npm install -g @anthropic-ai/claude-code`.
- Claude Code VS Code extension (`Anthropic.claude-code`).
- Common development tools: git, Python, Node/npm, ripgrep, jq, vim, nano.
- Common editor extensions for Python, YAML, Docker, formatting, linting, and Git.
- Docker CLI and Compose plugin installed inside this dev container only, so
  VS Code tasks can talk to a mounted pdocker Engine socket. The APK does not
  bundle an upstream Docker CLI binary for the product runtime path.

This library container template lives under the APK's shared `main` assets, so
the same dev workspace management helpers are available in release builds.
Authentication is required per user and per installed project: do not bake API tokens
or GitHub tokens, code-server passwords, SSH keys, signing keys, or Codex/Claude
state into this template or into the APK. Set user secrets at compose/runtime
time with `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, and
`CODE_SERVER_PASSWORD`.

## pdocker paths from VS Code

The default Compose project mounts app-owned pdocker paths into the container:

- `/workspace`: editable VS Code workspace for this dev project. The Android
  app maps this to app-private fast storage by default so editor state, build
  caches, and frequent logs do not constantly hit Documents/SD-card storage.
- `/pdocker/project`: this project directory, including its `Dockerfile`,
  `compose.yaml`, `scripts`, `vscode`, and `continue` folders.
- `/pdocker/projects`: the app project root, normally backed by the selected
  Android Documents folder at `pdocker/projects`.
- `/pdocker/host`: the app pdocker home, matching `filesDir/pdocker` on
  Android.
- `/pdocker/host/pdockerd.sock`: pdocker Engine socket when pdockerd is running.
- `/documents`: the selected Android Documents folder. Use this path when a
  containerized app explicitly needs to import, export, or exchange data on
  SD/Documents storage. Override with `PDOCKER_DOCUMENTS_HOST` or
  `PDOCKER_DOCUMENTS_MOUNT`.
- `/shared`: cross-project shared Documents volume. Point multiple projects at
  the same folder by setting `PDOCKER_SHARED_DOCUMENTS_HOST`.

Run `pdocker-paths` in the code-server terminal to see which paths are mounted
and whether an Engine socket is available.

## pdocker helpers

The image includes small helper commands in `/usr/local/bin`:

- `pdocker-paths`: show workspace, project, project-library, host, and Engine
  socket paths.
- `pdocker-projects`: list installed projects/templates under
  `/pdocker/projects`.
- `pdocker-new-project NAME [TEMPLATE]`: create a new project under
  `/pdocker/projects`. Use `blank` for a minimal template, or pass the name of
  an installed project/template shown by `pdocker-projects`.
- `pdocker-docker ...`: run `docker ...` after setting `DOCKER_HOST` only when
  a mounted socket/path is present.
- `pdocker-compose ...`: run `docker compose ...` through the same guarded
  Engine path.

If pdockerd is not running or its socket is not mounted, the Docker/Compose
helpers fail with a path-focused message instead of guessing a daemon.

Run from the pdocker UI:

1. Open `Dockerfile` tab.
2. Build the default project.
3. Open `Compose` tab and run the default compose project.
4. Open logs or shell from the `Containers` tab.

The app's current runtime uses host-style networking, so the code-server process
binds to `0.0.0.0:18080` inside the container by default. The compose header
comment `# pdocker.service-url: 18080=VS Code` labels the local browser
shortcut without changing standard Compose behavior. `# pdocker.auto-open: VS Code`
marks that declared service as the one pdocker may open automatically after
compose up.

If the Codex side panel is not obvious in code-server, open the command
palette and run `Tasks: Run Task`, then choose `Codex: start`. That task starts
the Codex CLI in a VS Code terminal inside `/workspace`.

Useful pdocker tasks are also available from `Tasks: Run Task`:

- `pdocker: show paths`
- `pdocker: list projects`
- `pdocker: create project from template`
- `pdocker: docker version`
- `pdocker: build current project`
- `pdocker: compose up current project`
- `pdocker: compose logs current project`

#!/usr/bin/env bash
set -euo pipefail

mkdir -p /workspace \
  "${CODE_SERVER_USER_DATA_DIR:-/workspace/.vscode-server/data}/User" \
  "${CODE_SERVER_EXTENSIONS_DIR:-/workspace/.vscode-server/extensions}" \
  /workspace/.continue

extensions_dir="${CODE_SERVER_EXTENSIONS_DIR:-/workspace/.vscode-server/extensions}"
image_extensions_dir="${CODE_SERVER_IMAGE_EXTENSIONS_DIR:-/opt/pdocker/code-server/extensions}"
install_extension_if_missing() {
  local ext="$1"
  local marker
  marker="$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')"
  if find "$extensions_dir" -maxdepth 1 -type d -iname "${marker}-*" | grep -q .; then
    return 0
  fi
  if [[ -d "$image_extensions_dir" ]]; then
    while IFS= read -r seeded; do
      cp -a "$seeded" "$extensions_dir/"
      return 0
    done < <(find "$image_extensions_dir" -maxdepth 1 -type d -iname "${marker}-*" | sort)
  fi
  echo "code-server extension install: $ext"
  code-server --extensions-dir "$extensions_dir" --install-extension "$ext"
}

for ext in Continue.continue OpenAI.chatgpt Anthropic.claude-code; do
  install_extension_if_missing "$ext"
done
for ext in redhat.vscode-yaml ms-azuretools.vscode-docker; do
  install_extension_if_missing "$ext" || true
done

if [[ -n "${CODE_SERVER_PASSWORD:-}" ]]; then
  export PASSWORD="$CODE_SERVER_PASSWORD"
  AUTH_MODE=password
else
  AUTH_MODE=none
fi
port="${CODE_SERVER_PORT:-18080}"

echo "code-server: http://0.0.0.0:$port"
echo "codex: $(command -v codex || true)"
echo "claude: $(command -v claude || true)"
echo "continue config: /workspace/.continue/config.yaml"
echo "Skydnir helpers: skydnir-paths, skydnir-projects, skydnir-new-project, skydnir-docker, skydnir-compose"
echo "Legacy aliases remain: pdocker-paths, pdocker-projects, pdocker-new-project, pdocker-docker, pdocker-compose"
pdocker-engine-env --check || true

exec code-server \
  --bind-addr "0.0.0.0:$port" \
  --auth "$AUTH_MODE" \
  --user-data-dir "${CODE_SERVER_USER_DATA_DIR:-/workspace/.vscode-server/data}" \
  --extensions-dir "$extensions_dir" \
  /workspace

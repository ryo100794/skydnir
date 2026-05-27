FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CODE_SERVER_VERSION=latest
ARG CODEX_NPM_PACKAGE=@openai/codex
ARG CLAUDE_CODE_NPM_PACKAGE=@anthropic-ai/claude-code
ARG NODE_MAJOR=22

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SHELL=/bin/bash \
    CODE_SERVER_USER_DATA_DIR=/workspace/.vscode-server/data \
    CODE_SERVER_EXTENSIONS_DIR=/workspace/.vscode-server/extensions \
    CODE_SERVER_IMAGE_EXTENSIONS_DIR=/opt/pdocker/code-server/extensions

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      dumb-init \
      git \
      gnupg \
      jq \
      openssh-client \
      procps \
      python3 \
      ripgrep \
      sudo \
      tar \
      unzip \
      libvulkan1 \
      wget \
      xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && npm --version \
    && rm -rf /var/lib/apt/lists/*

RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && docker --version \
    && docker compose version \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$CODE_SERVER_VERSION" = "latest" ]; then \
      curl -fsSL https://code-server.dev/install.sh | sh; \
    else \
      curl -fsSL https://code-server.dev/install.sh | sh -s -- --version "$CODE_SERVER_VERSION"; \
    fi

RUN npm install -g "$CODEX_NPM_PACKAGE" "$CLAUDE_CODE_NPM_PACKAGE"

RUN mkdir -p /workspace "$CODE_SERVER_USER_DATA_DIR/User" "$CODE_SERVER_EXTENSIONS_DIR" "$CODE_SERVER_IMAGE_EXTENSIONS_DIR" \
    && for ext in Continue.continue OpenAI.chatgpt Anthropic.claude-code; do \
         code-server --extensions-dir "$CODE_SERVER_IMAGE_EXTENSIONS_DIR" --install-extension "$ext"; \
       done \
    && for ext in redhat.vscode-yaml ms-azuretools.vscode-docker; do \
         code-server --extensions-dir "$CODE_SERVER_IMAGE_EXTENSIONS_DIR" --install-extension "$ext" || true; \
       done

COPY scripts/start-code-server.sh /usr/local/bin/start-code-server
COPY scripts/pdocker-* /usr/local/bin/
COPY vscode/settings.json /workspace/.vscode-server/data/User/settings.json
COPY continue/config.yaml /workspace/.continue/config.yaml
RUN chmod +x /usr/local/bin/start-code-server /usr/local/bin/pdocker-* \
    && for tool in compose docker engine-env new-project paths projects; do \
         ln -sf "pdocker-${tool}" "/usr/local/bin/skydnir-${tool}"; \
       done

WORKDIR /workspace
EXPOSE 18080

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/usr/local/bin/start-code-server"]

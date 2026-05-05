FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SHELL=/bin/sh

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      coreutils \
      dash \
      grep \
      procps \
      python3 \
      sed \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/pdocker-container-probe.sh /usr/local/bin/pdocker-container-probe
COPY scripts/start-direct-runtime-probe.sh /usr/local/bin/start-direct-runtime-probe
RUN chmod +x /usr/local/bin/pdocker-container-probe /usr/local/bin/start-direct-runtime-probe \
    && mkdir -p /workspace /reports /documents /shared

WORKDIR /workspace
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD grep -q '"status": "pass"' /reports/latest.json

CMD ["/usr/local/bin/start-direct-runtime-probe"]

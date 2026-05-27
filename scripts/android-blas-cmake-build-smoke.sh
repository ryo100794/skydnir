#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FLAVOR="${SKYDNIR_ANDROID_FLAVOR:-${PDOCKER_ANDROID_FLAVOR:-compat}}"
case "$FLAVOR" in
  compat) PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker.compat}" ;;
  modern) PKG="${PDOCKER_PACKAGE:-io.github.ryo100794.pdocker}" ;;
  *) echo "SKYDNIR_ANDROID_FLAVOR/PDOCKER_ANDROID_FLAVOR must be compat or modern" >&2; exit 2 ;;
esac

ADB="${ADB:-adb}"
PROJECT="${PDOCKER_BLAS_PROBE_PROJECT:-blas-cmake-probe}"
OUT_DIR="${PDOCKER_BLAS_PROBE_OUT_DIR:-$ROOT/docs/test}"
LOG="$OUT_DIR/android-blas-cmake-build-latest.log"
JSON="$OUT_DIR/android-blas-cmake-build-latest.json"

remote_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

run_adb() {
  "$ADB" "$@"
}

run_as() {
  run_adb shell "run-as $PKG sh -c $(remote_quote "$1")"
}

stage_test_cli() {
  local docker_bin="$ROOT/docker-proot-setup/docker-bin/docker"
  local compose_bin="$ROOT/vendor/lib/docker-compose"
  if [[ ! -x "$docker_bin" || ! -x "$compose_bin" ]]; then
    echo "test Docker CLI/Compose binaries missing" >&2
    exit 1
  fi
  run_adb push "$docker_bin" /data/local/tmp/pdocker-test-docker >/dev/null
  run_adb push "$compose_bin" /data/local/tmp/pdocker-test-docker-compose >/dev/null
  run_as "mkdir -p files/pdocker-runtime/docker-bin/cli-plugins && cp /data/local/tmp/pdocker-test-docker files/pdocker-runtime/docker-bin/docker && cp /data/local/tmp/pdocker-test-docker-compose files/pdocker-runtime/docker-bin/cli-plugins/docker-compose && chmod 755 files/pdocker-runtime/docker-bin/docker files/pdocker-runtime/docker-bin/cli-plugins/docker-compose"
}

wait_for_socket() {
  for _ in $(seq 1 45); do
    if run_as 'test -S files/pdocker/pdockerd.sock' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "pdockerd socket did not appear" >&2
  exit 1
}

mkdir -p "$OUT_DIR"
stage_test_cli
wait_for_socket

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

cat > "$tmpdir/Dockerfile" <<'DOCKERFILE'
FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SHELL=/bin/bash
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      ca-certificates \
      cmake \
      libopenblas-dev \
      ninja-build \
      pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /tmp/blas-probe && cd /tmp/blas-probe \
    && printf '%s\n' \
      'cmake_minimum_required(VERSION 3.22)' \
      'project(blas_probe C)' \
      'set(BLA_VENDOR OpenBLAS)' \
      'find_package(BLAS REQUIRED)' \
      'message(STATUS "PDocker BLAS_LIBRARIES=${BLAS_LIBRARIES}")' \
      > CMakeLists.txt \
    && cmake -S . -B build -G Ninja
DOCKERFILE

run_adb push "$tmpdir/Dockerfile" /data/local/tmp/pdocker-blas-probe-Dockerfile >/dev/null
set +e
run_as "rm -rf files/pdocker/projects/$PROJECT && mkdir -p files/pdocker/projects/$PROJECT && cp /data/local/tmp/pdocker-blas-probe-Dockerfile files/pdocker/projects/$PROJECT/Dockerfile && cd files && export PATH=\"\$PWD/pdocker-runtime/docker-bin:\$PATH\" DOCKER_CONFIG=\"\$PWD/pdocker-runtime/docker-bin\" DOCKER_HOST=\"unix://\$PWD/pdocker/pdockerd.sock\" DOCKER_BUILDKIT=0 BUILDKIT_PROGRESS=plain COMPOSE_PROGRESS=plain PDOCKER_DIRECT_TRACE_MODE=seccomp && docker build -t pdocker/$PROJECT:latest pdocker/projects/$PROJECT" >"$LOG" 2>&1
rc=$?
set -e

if grep -q "Could NOT find BLAS" "$LOG"; then
  rc=11
fi
if ! grep -Eq "Found BLAS|PDocker BLAS_LIBRARIES|Configuring done" "$LOG"; then
  rc=${rc:-12}
  [[ "$rc" == 0 ]] && rc=12
fi

python3 - "$LOG" "$JSON" "$rc" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

log = Path(sys.argv[1])
out = Path(sys.argv[2])
rc = int(sys.argv[3])
data = log.read_bytes()
out.write_text(json.dumps({
    "schema": "pdocker.android.blas-cmake-build.v1",
    "command": "scripts/android-blas-cmake-build-smoke.sh",
    "exit_code": rc,
    "log": str(log),
    "log_sha256": hashlib.sha256(data).hexdigest(),
    "log_size": len(data),
    "blas_not_found": b"Could NOT find BLAS" in data,
    "found_blas": b"Found BLAS" in data or b"PDocker BLAS_LIBRARIES" in data,
}, indent=2, sort_keys=True) + "\n")
PY

cat "$LOG"
exit "$rc"

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG LLAMA_CPP_REF=b9030
ARG LLAMA_CPP_BUILD_JOBS=1
ARG LLAMA_CPP_BUILD_TYPE=Release

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SHELL=/bin/bash

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      git \
      glslc \
      glslang-tools \
      jq \
      libopenblas-dev \
      libvulkan-dev \
      libvulkan1 \
      ninja-build \
      pkg-config \
      procps \
      python3 \
      python3-pip \
      spirv-headers \
      spirv-tools \
      vulkan-tools \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp /opt/llama.cpp \
    && cd /opt/llama.cpp \
    && git config --global --add safe.directory /opt/llama.cpp \
    && if [ "$LLAMA_CPP_REF" != "master" ]; then git fetch --depth 1 origin "$LLAMA_CPP_REF" && git checkout --detach FETCH_HEAD; fi \
    && echo "pdocker llama.cpp ref=$(git rev-parse HEAD) tag=$LLAMA_CPP_REF build_type=$LLAMA_CPP_BUILD_TYPE jobs=$LLAMA_CPP_BUILD_JOBS" \
    && git rev-parse HEAD > /opt/llama.cpp/.pdocker-llama-cpp-commit \
    && cmake -B build -G Ninja \
      -DCMAKE_BUILD_TYPE="$LLAMA_CPP_BUILD_TYPE" \
      -DLLAMA_BUILD_TESTS=OFF \
      -DLLAMA_BUILD_EXAMPLES=OFF \
      -DLLAMA_BUILD_WEBUI=ON \
      -DLLAMA_OPENSSL=OFF \
      -DGGML_NATIVE=OFF \
      -DGGML_OPENMP=OFF \
      -DGGML_VULKAN=ON \
      -DGGML_VULKAN_CHECK_RESULTS=OFF \
      -DGGML_VULKAN_DEBUG=OFF \
      -DGGML_VULKAN_MEMORY_DEBUG=OFF \
      -DGGML_VULKAN_SHADER_DEBUG_INFO=OFF \
      -DGGML_VULKAN_VALIDATE=OFF \
      -DGGML_VULKAN_RUN_TESTS=OFF \
      -DGGML_BLAS=ON \
      -DGGML_BLAS_VENDOR=OpenBLAS \
    && jobs="${LLAMA_CPP_BUILD_JOBS:-1}" \
    && cmake --build build --config "$LLAMA_CPP_BUILD_TYPE" --target llama-server --parallel "$jobs"

COPY scripts/pdocker-gpu-profile.sh /usr/local/bin/pdocker-gpu-profile
COPY scripts/start-llama-server.sh /usr/local/bin/start-llama-server
COPY scripts/pdocker-llama-correctness.sh /usr/local/bin/pdocker-llama-correctness
RUN chmod +x /usr/local/bin/pdocker-gpu-profile /usr/local/bin/start-llama-server /usr/local/bin/pdocker-llama-correctness \
    && mkdir -p /models /workspace /workspace/logs /profiles /var/log/pdocker

WORKDIR /workspace
EXPOSE 18081
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=6 CMD curl -fsS http://127.0.0.1:18081/health >/dev/null || curl -fsS http://127.0.0.1:18081/v1/models >/dev/null

CMD ["/usr/local/bin/start-llama-server"]

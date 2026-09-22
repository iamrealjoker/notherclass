# Dockerfile — NotherClass (agente + consola web), portable.
# Base Ubuntu LTS. STT (whisper) y TTS (piper) son binarios locales -> 0 tokens.
#
# Uso:
#   docker build -t notherclass .
#   docker run --rm -it -v "$PWD/agent/.env:/app/notherclass/agent/.env" notherclass
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_NO_CACHE_DIR=1

# ── 1) sistema base: python3, herramientas, audio ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-requests \
    ffmpeg espeak-ng git cmake build-essential wget curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── 2) dependencias python (la mayoría es stdlib) ──
RUN pip3 install --no-cache-dir numpy requests Flask

# ── 3) whisper.cpp (STT local) en /opt/whisper.cpp ──
RUN git clone --depth 1 https://github.com/ggerganov/whisper.cpp /opt/whisper.cpp \
 && cd /opt/whisper.cpp && cmake -B build && cmake --build build --config Release --parallel 2 \
 && wget -q -O models/ggml-base.bin \
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin

# ── 4) piper (TTS natural) en /opt/piper ──
RUN cd /opt && wget -q \
    https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz \
    -O piper.tgz && mkdir -p piper && tar -xzf piper.tgz -C piper --strip-components=1 \
    && rm piper.tgz
ENV LD_LIBRARY_PATH=/opt/piper
ENV PIPER_DATA_DIR=/opt/piper

# ── 5) código (el .dockerignore deja fuera datos, secretos y entornos) ──
WORKDIR /app
COPY . /app/notherclass

# el agente arranca desde agent/ ; su workspace es ../ (raíz del proyecto)
WORKDIR /app/notherclass/agent

# ── 6) python3.11 + venv con faster-whisper (STT rápido) en agent/venv311 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
      software-properties-common gnupg ca-certificates \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
 && rm -rf /var/lib/apt/lists/*
RUN python3.11 -m venv venv311 \
 && venv311/bin/pip install --no-cache-dir faster-whisper

# El agente principal es el proceso que arranca; él levanta el resto (supervisor).
CMD ["python3", "run.py"]


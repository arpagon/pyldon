# Qwen3-TTS Server Dockerfile
# Multi-stage build for optimized image size

# ============================================================================
# Stage 1: Base with CUDA and Python
# ============================================================================
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and Python 3.12 from deadsnakes PPA
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    ca-certificates \
    curl \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    build-essential \
    git \
    libsndfile1 \
    ffmpeg \
    sox \
    libsox-fmt-all \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3

# Install pip using get-pip.py (proper way for Python 3.12)
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

# Set up Python environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# ============================================================================
# Stage 2: Dependencies
# ============================================================================
FROM base AS dependencies

WORKDIR /app

# Upgrade pip and install build tools
RUN python -m pip install --upgrade pip setuptools wheel

# Install PyTorch with CUDA support (using default CUDA version for compatibility)
RUN pip install torch torchvision torchaudio

# Install qwen-tts and core dependencies
RUN pip install qwen-tts

# Install flash-attention only for Ampere+ GPUs (compute capability >= 8.0)
# Skip for Turing (GTX 16xx, RTX 20xx) and older
# RUN MAX_JOBS=4 pip install flash-attn --no-build-isolation || \
#     echo "FlashAttention installation skipped"

# Install web framework dependencies
RUN pip install \
    "gradio>=4.0.0" \
    "fastapi>=0.100.0" \
    "uvicorn[standard]>=0.22.0" \
    python-multipart \
    "pydantic>=2.0.0" \
    soundfile \
    aiofiles

# ============================================================================
# Stage 3: Final Application
# ============================================================================
FROM dependencies AS final

WORKDIR /app

# Pre-download the model during build (optional - makes container larger but faster startup)
# Comment out if you prefer to download on first run
RUN python -c "from qwen_tts import Qwen3TTSModel; \
    Qwen3TTSModel.from_pretrained('Qwen/Qwen3-TTS-12Hz-0.6B-Base', \
    device_map='cpu', dtype='auto')" || echo "Model pre-download skipped"

# Copy application code
COPY tts_model.py .
COPY gradio_app.py .
COPY openai_api.py .
COPY server.py .

# Create voices directory mount point
RUN mkdir -p /app/voices

# Expose ports
# 3010: Gradio UI
# 3011: OpenAI-compatible API
EXPOSE 3010 3011

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:3011/health || exit 1

# Default command - run combined server
CMD ["python", "server.py"]

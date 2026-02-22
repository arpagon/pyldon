FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYLDON_STT_MODEL=nemo-parakeet-tdt-0.6b-v3

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# onnx-asr with GPU support — much lighter than NeMo+PyTorch
RUN pip3 install --break-system-packages --no-cache-dir \
    'onnx-asr[gpu,hub]'

# Pre-download model at build time
RUN python3 -c "\
import onnx_asr; \
m = onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3'); \
print('Model loaded OK:', type(m))"

COPY transcribe.py /app/transcribe.py

WORKDIR /app
ENTRYPOINT ["python3", "/app/transcribe.py"]

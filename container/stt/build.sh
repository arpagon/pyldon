#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="pyldon-stt:latest"

echo "Building $IMAGE_NAME ..."
echo "WARNING: This will download NeMo + PyTorch + Parakeet model (~8GB+ image)"
echo ""

docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

echo ""
echo "Done: $IMAGE_NAME"
echo "Test:"
echo "  docker run --rm --gpus all $IMAGE_NAME --help"

#!/bin/bash
# Build the Pyldon agent container image
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

IMAGE_NAME="pyldon-agent"
TAG="${1:-latest}"

echo "Building ${IMAGE_NAME}:${TAG}..."
docker build -t "${IMAGE_NAME}:${TAG}" .

echo ""
echo "Done: ${IMAGE_NAME}:${TAG}"
echo "Test:"
echo "  echo '{\"prompt\":\"What is 2+2?\",\"groupFolder\":\"test\",\"chatJid\":\"test\",\"isMain\":false}' | docker run -i --rm -e ANTHROPIC_API_KEY=\$ANTHROPIC_API_KEY ${IMAGE_NAME}:${TAG}"

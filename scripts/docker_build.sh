#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_NAME="${IMAGE_NAME:-site-agent-eval:local}"

docker build -t "$IMAGE_NAME" .

echo "Built Docker image: $IMAGE_NAME"

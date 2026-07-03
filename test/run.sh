#!/usr/bin/env bash
# Builds the install.sh test image and runs the scenario suite against a
# throwaway container. Mounts the repo read-only so nothing here can touch
# the real machine or the checked-out working tree.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
IMAGE=dotfiles-install-test

echo "==> Building test image..."
docker build -q -t "$IMAGE" "$HERE" >/dev/null

echo "==> Running scenario suite..."
docker run --rm \
  -v "$REPO:/dotfiles:ro" \
  -w /home/tester \
  "$IMAGE" \
  bash -c 'cp -r /dotfiles /home/tester/dotfiles && bash /home/tester/dotfiles/test/scenarios.sh'

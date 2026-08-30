#!/usr/bin/env bash
set -euo pipefail

docker --version
docker buildx version
docker compose version

if [[ "${EXPECT_DOCKER_SOCKET:-0}" == 1 ]]; then
  docker version
  tmp="$(mktemp -d)"
  trap 'docker image rm ci-images-socket-smoke:local >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
  printf 'FROM scratch\n' >"$tmp/Dockerfile"
  docker build -t ci-images-socket-smoke:local "$tmp"
  docker image inspect ci-images-socket-smoke:local >/dev/null
fi

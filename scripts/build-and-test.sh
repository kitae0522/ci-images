#!/usr/bin/env bash
set -euo pipefail

docker build --platform linux/amd64 -t ci-ubuntu-base:test -f images/base/Dockerfile .
docker run --rm -i ci-ubuntu-base:test bash -s <tests/base-contract.sh
docker build --platform linux/amd64 --build-arg BASE_IMAGE=ci-ubuntu-base:test \
  -t ci-ubuntu-docker:test -f images/docker/Dockerfile .
docker run --rm -i ci-ubuntu-docker:test bash -s <tests/base-contract.sh
docker run --rm -i ci-ubuntu-docker:test bash -s <tests/docker-contract.sh

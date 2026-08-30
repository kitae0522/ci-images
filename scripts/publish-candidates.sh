#!/usr/bin/env bash
set -euo pipefail
short_sha="${GITHUB_SHA:0:7}"
base_ref="ghcr.io/kitae0522/ci-ubuntu-base:sha-${short_sha}"
docker_ref="ghcr.io/kitae0522/ci-ubuntu-docker:sha-${short_sha}"
docker tag ci-ubuntu-base:test "$base_ref"
docker tag ci-ubuntu-docker:test "$docker_ref"
docker push "$base_ref"
docker push "$docker_ref"
base_digest="$(docker buildx imagetools inspect "$base_ref" --format '{{json .Manifest}}' | jq -r .digest)"
docker_digest="$(docker buildx imagetools inspect "$docker_ref" --format '{{json .Manifest}}' | jq -r .digest)"
test "$base_digest" != null
test "$docker_digest" != null
printf 'short_sha=%s\nbase_digest=%s\ndocker_digest=%s\n' \
  "$short_sha" "$base_digest" "$docker_digest" >>"${GITHUB_OUTPUT:?}"

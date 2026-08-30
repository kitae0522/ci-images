#!/usr/bin/env bash
set -euo pipefail
source_sha="${GITHUB_SHA:?GITHUB_SHA is required}"
run_id="${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
run_attempt="${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
short_sha="${source_sha:0:7}"
candidate_tag="sha-${short_sha}-${run_id}-${run_attempt}"
base_ref="ghcr.io/kitae0522/ci-ubuntu-base:${candidate_tag}"
docker_ref="ghcr.io/kitae0522/ci-ubuntu-docker:${candidate_tag}"
docker tag ci-ubuntu-base:test "$base_ref"
docker tag ci-ubuntu-docker:test "$docker_ref"
docker push "$base_ref"
docker push "$docker_ref"
base_digest="$(docker buildx imagetools inspect "$base_ref" --format '{{json .Manifest}}' | jq -r .digest)"
docker_digest="$(docker buildx imagetools inspect "$docker_ref" --format '{{json .Manifest}}' | jq -r .digest)"
test "$base_digest" != null
test "$docker_digest" != null
printf 'candidate_tag=%s\nbase_digest=%s\ndocker_digest=%s\n' \
  "$candidate_tag" "$base_digest" "$docker_digest" >>"${GITHUB_OUTPUT:?}"

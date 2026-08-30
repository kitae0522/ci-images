#!/usr/bin/env bash
set -euo pipefail
base_digest="${1:?base digest required}"
docker_digest="${2:?docker digest required}"
date_tag="24.04-$(date -u +%Y%m%d)"
promote() {
  local image="$1" digest="$2"
  if ! docker buildx imagetools inspect "$image:$date_tag" >/dev/null 2>&1; then
    docker buildx imagetools create -t "$image:$date_tag" "$image@$digest"
  fi
  docker buildx imagetools create -t "$image:24.04" "$image@$digest"
}
promote ghcr.io/kitae0522/ci-ubuntu-base "$base_digest"
promote ghcr.io/kitae0522/ci-ubuntu-docker "$docker_digest"

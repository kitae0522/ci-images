#!/usr/bin/env bash
set -euo pipefail
base_digest="${1:?base digest required}"
docker_digest="${2:?docker digest required}"
date_tag="24.04-$(date -u +%Y%m%d)"

manifest_is_missing() {
  local output="${1,,}"
  case "$output" in
    *unauthorized*|*authentication\ required*|*forbidden*|*denied*|*registry*|\
    *unexpected\ status*|*network*|*connection*|*timeout*|*no\ such\ host*|\
    *temporary\ failure*|*500*|*502*|*503*|*504*)
      return 1
      ;;
  esac
  case "$output" in
    *no\ such\ manifest*|*manifest\ unknown*|*manifest\ not\ found*|*:\ not\ found*|*404*|not\ found)
      return 0
      ;;
  esac
  return 1
}

inspect_dated_tag() {
  local image="$1" output
  if output="$(docker buildx imagetools inspect "$image:$date_tag" 2>&1)"; then
    return 0
  fi
  if manifest_is_missing "$output"; then
    return 1
  fi
  printf 'Unable to inspect existing dated tag %s:%s: %s\n' \
    "$image" "$date_tag" "$output" >&2
  return 2
}

prepare_dated_tag() {
  local image="$1" digest="$2" inspect_status
  if inspect_dated_tag "$image"; then
    return 0
  else
    inspect_status=$?
  fi
  if (( inspect_status != 1 )); then
    return "$inspect_status"
  fi
  docker buildx imagetools create -t "$image:$date_tag" "$image@$digest"
}

# Decide and create dated tags before moving either stable tag. This prevents a
# partial stable promotion when the second dated-tag inspection fails closed.
prepare_dated_tag ghcr.io/kitae0522/ci-ubuntu-base "$base_digest"
prepare_dated_tag ghcr.io/kitae0522/ci-ubuntu-docker "$docker_digest"
docker buildx imagetools create -t ghcr.io/kitae0522/ci-ubuntu-base:24.04 \
  ghcr.io/kitae0522/ci-ubuntu-base@"$base_digest"
docker buildx imagetools create -t ghcr.io/kitae0522/ci-ubuntu-docker:24.04 \
  ghcr.io/kitae0522/ci-ubuntu-docker@"$docker_digest"

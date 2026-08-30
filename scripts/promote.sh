#!/usr/bin/env bash
set -euo pipefail
base_digest="${1:?base digest required}"
docker_digest="${2:?docker digest required}"
date_tag="24.04-$(date -u +%Y%m%d)"

manifest_is_missing() {
  local output="${1,,}" target="${2,,}"
  case "$output" in
    *unauthorized*|*authentication\ required*|*forbidden*|*denied*|*registry*|\
    *unexpected\ status*|*network*|*connection*|*timeout*|*no\ such\ host*|\
    *temporary\ failure*|*500*|*502*|*503*|*504*)
      return 1
      ;;
  esac
  if [[ "$output" == *"$target: not found"* ]]; then
    return 0
  fi
  case "$output" in
    *no\ such\ manifest*|*manifest\ unknown*|*manifest\ not\ found*|\
    *failed\ to\ resolve*not\ found*|*404\ not\ found*|not\ found)
      return 0
      ;;
  esac
  return 1
}

inspect_dated_tag() {
  local image="$1" expected_digest="$2" output actual_digest
  if output="$(docker buildx imagetools inspect "$image:$date_tag" \
    --format '{{json .Manifest}}' 2>&1)"; then
    if ! actual_digest="$(jq -er \
      '.digest | select(type == "string" and test("^sha256:[0-9a-f]{64}$"))' \
      <<<"$output")"; then
      printf 'Unable to parse manifest digest for %s:%s: %s\n' \
        "$image" "$date_tag" "$output" >&2
      return 2
    fi
    if [[ "$actual_digest" != "$expected_digest" ]]; then
      printf 'Existing dated tag %s:%s has digest %s, expected %s.\n' \
        "$image" "$date_tag" "$actual_digest" "$expected_digest" >&2
      return 2
    fi
    return 0
  fi
  if manifest_is_missing "$output" "$image:$date_tag"; then
    return 1
  fi
  printf 'Unable to inspect existing dated tag %s:%s: %s\n' \
    "$image" "$date_tag" "$output" >&2
  return 2
}

require_exact_digest() {
  local reference="$1" expected_digest="$2" output actual_digest
  if ! output="$(docker buildx imagetools inspect "$reference" \
    --format '{{json .Manifest}}' 2>&1)"; then
    printf 'Unable to verify promoted reference %s: %s\n' \
      "$reference" "$output" >&2
    return 1
  fi
  if ! actual_digest="$(jq -er \
    '.digest | select(type == "string" and test("^sha256:[0-9a-f]{64}$"))' \
    <<<"$output")"; then
    printf 'Unable to parse promoted digest for %s: %s\n' \
      "$reference" "$output" >&2
    return 1
  fi
  if [[ "$actual_digest" != "$expected_digest" ]]; then
    printf 'Promoted reference %s has digest %s, expected %s.\n' \
      "$reference" "$actual_digest" "$expected_digest" >&2
    return 1
  fi
}

base_dated_status=0
docker_dated_status=0
if inspect_dated_tag ghcr.io/kitae0522/ci-ubuntu-base "$base_digest"; then
  base_dated_status=0
else
  base_dated_status=$?
fi
if inspect_dated_tag ghcr.io/kitae0522/ci-ubuntu-docker "$docker_digest"; then
  docker_dated_status=0
else
  docker_dated_status=$?
fi

if (( base_dated_status == 2 || docker_dated_status == 2 )); then
  echo "Aborting promotion because a dated-tag inspection failed." >&2
  exit 1
fi
if (( base_dated_status != docker_dated_status )); then
  echo "Aborting promotion because dated tags are in a mixed state." >&2
  exit 1
fi

if (( base_dated_status == 1 )); then
  docker buildx imagetools create --prefer-index=false \
    -t ghcr.io/kitae0522/ci-ubuntu-base:"$date_tag" \
    ghcr.io/kitae0522/ci-ubuntu-base@"$base_digest"
  docker buildx imagetools create --prefer-index=false \
    -t ghcr.io/kitae0522/ci-ubuntu-docker:"$date_tag" \
    ghcr.io/kitae0522/ci-ubuntu-docker@"$docker_digest"
fi

require_exact_digest \
  ghcr.io/kitae0522/ci-ubuntu-base:"$date_tag" "$base_digest"
require_exact_digest \
  ghcr.io/kitae0522/ci-ubuntu-docker:"$date_tag" "$docker_digest"

# Both dated-tag decisions are complete before either stable tag moves.
docker buildx imagetools create --prefer-index=false \
  -t ghcr.io/kitae0522/ci-ubuntu-base:24.04 \
  ghcr.io/kitae0522/ci-ubuntu-base@"$base_digest"
docker buildx imagetools create --prefer-index=false \
  -t ghcr.io/kitae0522/ci-ubuntu-docker:24.04 \
  ghcr.io/kitae0522/ci-ubuntu-docker@"$docker_digest"
require_exact_digest ghcr.io/kitae0522/ci-ubuntu-base:24.04 "$base_digest"
require_exact_digest ghcr.io/kitae0522/ci-ubuntu-docker:24.04 "$docker_digest"

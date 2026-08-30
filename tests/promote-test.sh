#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mock_bin="$tmp/bin"
mkdir -p "$mock_bin"

cat >"$mock_bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${MOCK_DOCKER_LOG:?}"
if [[ "$1" == buildx && "$2" == imagetools && "$3" == inspect ]]; then
  case "${MOCK_INSPECT_MODE:?}" in
    existing)
      exit 0
      ;;
    missing)
      printf 'ERROR: %s: not found\n' "${4:?}" >&2
      exit 1
      ;;
    mixed)
      if [[ "${4:?}" == *ci-ubuntu-base:* ]]; then
        printf 'ERROR: no such manifest: %s\n' "$4" >&2
        exit 1
      fi
      exit 0
      ;;
    mixed-reverse)
      if [[ "${4:?}" == *ci-ubuntu-base:* ]]; then
        exit 0
      fi
      printf 'ERROR: no such manifest: %s\n' "$4" >&2
      exit 1
      ;;
    second-registry)
      if [[ "${4:?}" == *ci-ubuntu-base:* ]]; then
        printf 'ERROR: no such manifest: %s\n' "$4" >&2
      else
        printf 'ERROR: registry request failed: resource not found\n' >&2
      fi
      exit 1
      ;;
    second-auth)
      if [[ "${4:?}" == *ci-ubuntu-base:* ]]; then
        printf 'ERROR: no such manifest: %s\n' "$4" >&2
      else
        printf 'ERROR: unauthorized: authentication required\n' >&2
      fi
      exit 1
      ;;
    second-network)
      if [[ "${4:?}" == *ci-ubuntu-base:* ]]; then
        printf 'ERROR: no such manifest: %s\n' "$4" >&2
      else
        printf 'ERROR: dial tcp: network is unreachable\n' >&2
      fi
      exit 1
      ;;
    second-unknown)
      if [[ "${4:?}" == *ci-ubuntu-base:* ]]; then
        printf 'ERROR: no such manifest: %s\n' "$4" >&2
      else
        printf 'ERROR: unexpected error: not found\n' >&2
      fi
      exit 1
      ;;
    *)
      echo "unknown inspect mode" >&2
      exit 1
      ;;
  esac
fi
MOCK_DOCKER
chmod +x "$mock_bin/docker"

run_promote() {
  local mode="$1" log="$2"
  : >"$log"
  MOCK_DOCKER_LOG="$log" MOCK_INSPECT_MODE="$mode" PATH="$mock_bin:$PATH" \
    "$repo_root/scripts/promote.sh" sha256:base sha256:docker
}

count_commands() {
  local pattern="$1" log="$2"
  grep -c "$pattern" "$log" || true
}

date_tag="24.04-$(date -u +%Y%m%d)"

run_promote existing "$tmp/existing.log"
test "$(count_commands '^buildx imagetools inspect ' "$tmp/existing.log")" -eq 2
test "$(count_commands "^buildx imagetools create -t .*:$date_tag " "$tmp/existing.log")" -eq 0
test "$(count_commands '^buildx imagetools create -t .*:24.04 ' "$tmp/existing.log")" -eq 2

run_promote missing "$tmp/missing.log"
test "$(count_commands '^buildx imagetools inspect ' "$tmp/missing.log")" -eq 2
test "$(count_commands "^buildx imagetools create -t .*:$date_tag " "$tmp/missing.log")" -eq 2
test "$(count_commands '^buildx imagetools create -t .*:24.04 ' "$tmp/missing.log")" -eq 2
second_inspect_line="$(grep -n -F 'buildx imagetools inspect ghcr.io/kitae0522/ci-ubuntu-docker:' "$tmp/missing.log" | cut -d: -f1)"
first_create_line="$(grep -n -F "buildx imagetools create -t ghcr.io/kitae0522/ci-ubuntu-base:$date_tag " "$tmp/missing.log" | cut -d: -f1)"
test "$second_inspect_line" -lt "$first_create_line"
for image in ghcr.io/kitae0522/ci-ubuntu-base ghcr.io/kitae0522/ci-ubuntu-docker; do
  dated_line="$(grep -n -F "buildx imagetools create -t $image:$date_tag " "$tmp/missing.log" | cut -d: -f1)"
  stable_line="$(grep -n -F "buildx imagetools create -t $image:24.04 " "$tmp/missing.log" | cut -d: -f1)"
  test "$dated_line" -lt "$stable_line"
done

for mode in mixed mixed-reverse; do
  if run_promote "$mode" "$tmp/$mode.log" 2>"$tmp/$mode.err"; then
    echo "promotion unexpectedly succeeded for $mode dated-tag state" >&2
    exit 1
  fi
  test "$(count_commands '^buildx imagetools inspect ' "$tmp/$mode.log")" -eq 2
  test "$(count_commands '^buildx imagetools create ' "$tmp/$mode.log")" -eq 0
done

for mode in second-registry second-auth second-network second-unknown; do
  log="$tmp/$mode.log"
  if run_promote "$mode" "$log" 2>"$tmp/$mode.err"; then
    echo "promotion unexpectedly succeeded for $mode" >&2
    exit 1
  fi
  test "$(count_commands '^buildx imagetools inspect ' "$log")" -eq 2
  test "$(count_commands '^buildx imagetools create ' "$log")" -eq 0
done

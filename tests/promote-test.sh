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
      printf 'ERROR: no such manifest: %s\n' "${4:?}" >&2
      exit 1
      ;;
    registry)
      printf 'ERROR: registry request failed: resource not found\n' >&2
      exit 1
      ;;
    auth)
      printf 'ERROR: unauthorized: authentication required\n' >&2
      exit 1
      ;;
    network)
      printf 'ERROR: dial tcp: network is unreachable\n' >&2
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

date_tag="24.04-$(date -u +%Y%m%d)"

run_promote existing "$tmp/existing.log"
test "$(grep -c '^buildx imagetools inspect ' "$tmp/existing.log")" -eq 2
test "$(grep -c "^buildx imagetools create -t .*:$date_tag " "$tmp/existing.log")" -eq 0
test "$(grep -c '^buildx imagetools create -t .*:24.04 ' "$tmp/existing.log")" -eq 2

run_promote missing "$tmp/missing.log"
test "$(grep -c '^buildx imagetools inspect ' "$tmp/missing.log")" -eq 2
test "$(grep -c "^buildx imagetools create -t .*:$date_tag " "$tmp/missing.log")" -eq 2
test "$(grep -c '^buildx imagetools create -t .*:24.04 ' "$tmp/missing.log")" -eq 2
for image in ghcr.io/kitae0522/ci-ubuntu-base ghcr.io/kitae0522/ci-ubuntu-docker; do
  dated_line="$(grep -n -F "buildx imagetools create -t $image:$date_tag " "$tmp/missing.log" | cut -d: -f1)"
  stable_line="$(grep -n -F "buildx imagetools create -t $image:24.04 " "$tmp/missing.log" | cut -d: -f1)"
  test "$dated_line" -lt "$stable_line"
done

for mode in registry auth network; do
  log="$tmp/$mode.log"
  if run_promote "$mode" "$log"; then
    echo "promotion unexpectedly succeeded for $mode" >&2
    exit 1
  fi
  test "$(grep -c '^buildx imagetools inspect ' "$log")" -eq 1
  test "$(grep -c '^buildx imagetools create ' "$log" || true)" -eq 0
done

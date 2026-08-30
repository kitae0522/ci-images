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
  printf '{"digest":"%s"}\n' "${MOCK_DIGEST:?}"
fi
MOCK_DOCKER
chmod +x "$mock_bin/docker"

cat >"$mock_bin/jq" <<'MOCK_JQ'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == -r && "${2:-}" == .digest ]]; then
  printf '%s\n' "${MOCK_DIGEST:?}"
else
  echo "unexpected jq invocation: $*" >&2
  exit 1
fi
MOCK_JQ
chmod +x "$mock_bin/jq"

run_publish() {
  local run_id="$1" attempt="$2" output="$3"
  : >"$MOCK_DOCKER_LOG"
  : >"$output"
  GITHUB_SHA=0123456789abcdef0123456789abcdef01234567 \
    GITHUB_RUN_ID="$run_id" \
    GITHUB_RUN_ATTEMPT="$attempt" \
    GITHUB_OUTPUT="$output" \
    MOCK_DOCKER_LOG="$MOCK_DOCKER_LOG" \
    MOCK_DIGEST=sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
    PATH="$mock_bin:$PATH" \
    "$repo_root/scripts/publish-candidates.sh"
}

MOCK_DOCKER_LOG="$tmp/docker.log"
run_publish 100 1 "$tmp/first.output"
cp "$MOCK_DOCKER_LOG" "$tmp/first.log"
run_publish 100 2 "$tmp/second.output"
cp "$MOCK_DOCKER_LOG" "$tmp/second.log"

grep -Fq 'candidate_tag=sha-0123456-100-1' "$tmp/first.output"
grep -Fq 'candidate_tag=sha-0123456-100-2' "$tmp/second.output"
grep -Fq 'tag ci-ubuntu-base:test ghcr.io/kitae0522/ci-ubuntu-base:sha-0123456-100-1' "$tmp/first.log"
grep -Fq 'tag ci-ubuntu-docker:test ghcr.io/kitae0522/ci-ubuntu-docker:sha-0123456-100-1' "$tmp/first.log"
grep -Fq 'tag ci-ubuntu-base:test ghcr.io/kitae0522/ci-ubuntu-base:sha-0123456-100-2' "$tmp/second.log"
grep -Fq 'tag ci-ubuntu-docker:test ghcr.io/kitae0522/ci-ubuntu-docker:sha-0123456-100-2' "$tmp/second.log"

test "$(grep -c '^tag ci-ubuntu-base:test ' "$tmp/first.log")" -eq 1
test "$(grep -c '^tag ci-ubuntu-base:test ' "$tmp/second.log")" -eq 1
test "$(grep -F 'tag ci-ubuntu-base:test ' "$tmp/first.log" | awk '{print $3}')" != \
  "$(grep -F 'tag ci-ubuntu-base:test ' "$tmp/second.log" | awk '{print $3}')"

grep -Fq 'base_digest=sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' "$tmp/first.output"
grep -Fq 'docker_digest=sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' "$tmp/first.output"

#!/usr/bin/env bash
set -euo pipefail

test -s /etc/ssl/certs/ca-certificates.crt
curl --fail --silent --show-error --location https://proxy.golang.org/ >/dev/null
for command in git git-lfs ssh curl wget gpg jq zip unzip tar xz bzip2 zstd \
               gcc g++ make pkg-config cmake ninja python3; do
  command -v "$command" >/dev/null
done
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf 'int main(void) { return 0; }\n' >"$tmp/main.c"
cc "$tmp/main.c" -o "$tmp/hello"
"$tmp/hello"
test "$(locale charmap)" = UTF-8

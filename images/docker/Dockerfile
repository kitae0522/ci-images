ARG BASE_IMAGE=ci-ubuntu-base:test
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl --fail --silent --show-error --location \
      https://download.docker.com/linux/ubuntu/gpg \
      --output /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && printf '%s\n' \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
      >/etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      docker-ce-cli docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.source="https://github.com/kitae0522/ci-images"
LABEL org.opencontainers.image.description="Ubuntu 24.04 CI image with Docker client tooling"

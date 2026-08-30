FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash build-essential bzip2 ca-certificates cmake curl file git git-lfs \
      gnupg jq locales make ninja-build openssh-client pkg-config python3 tar \
      tzdata unzip wget xz-utils zip zstd \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
LABEL org.opencontainers.image.source="https://github.com/kitae0522/ci-images"
LABEL org.opencontainers.image.description="Ubuntu 24.04 base for trusted self-hosted CI"

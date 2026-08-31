FROM ubuntu:26.04@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash build-essential bzip2 ca-certificates cmake curl file git git-lfs \
      gnupg jq locales make ninja-build openssh-client pkg-config python3 tar \
      tzdata unzip wget xz-utils zip zstd \
      fonts-liberation libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 \
      libcairo2 libcups2t64 libdrm2 libgbm1 libglib2.0-0t64 libnspr4 libnss3 \
      libpango-1.0-0 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 \
      libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
LABEL org.opencontainers.image.source="https://github.com/kitae0522/ci-images"
LABEL org.opencontainers.image.description="Ubuntu 24.04 base for trusted self-hosted CI"

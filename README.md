# Reusable CI images

This public repository publishes small Ubuntu 24.04 images for trusted,
self-hosted GitHub Actions jobs. Images target `linux/amd64` and share the
resource contract used by the `lab` runner.

## Images and contents

`ci-ubuntu-base` includes:

- `bash`, `ca-certificates`, `tzdata`, and generated `en_US.UTF-8` locale;
- `git`, `git-lfs`, and `openssh-client`;
- `curl`, `wget`, `gnupg`, and `jq`;
- `zip`, `unzip`, `tar`, `xz-utils`, `bzip2`, and `zstd`;
- `build-essential` (`gcc`, `g++`, and `make`), `pkg-config`, `cmake`,
  `ninja-build`, `python3`, and `file`.

`ci-ubuntu-docker` extends the base image with `docker-ce-cli`, Buildx, and
Compose. It contains Docker client tools only, not a Docker daemon.

The images deliberately exclude Go, Node.js, Rust, Java, project database
clients, cloud CLIs, credentials, private source, SSH keys, Nix secrets, and a
Docker daemon. Workflows choose language versions with `setup-*` actions.

## Tags and reproducibility

- `:sha-abcdef1` is the immutable source-commit candidate and canonical
  rollback identifier.
- `:24.04-YYYYMMDD` is an immutable dated release and is never overwritten.
- `:24.04` is the rolling stable tag, moved only after lab smoke succeeds.
- An image digest (`@sha256:...`) is the strongest reproducibility reference.

Pin a digest or dated tag when a workflow needs an immutable reference. A
failed smoke job leaves the previous stable digest unchanged. To roll back,
pin consumers to the previous digest/date tag or, from the hosted promotion
job with package write permission, retag that already-published digest; never
rebuild unknown source under an old tag.

## Validation and release flow

Run the local contract gate from the repository root:

```bash
scripts/build-and-test.sh
nix shell nixpkgs#actionlint -c actionlint
nix shell nixpkgs#hadolint -c hadolint --config .hadolint.yaml \
  images/base/Dockerfile images/docker/Dockerfile
```

The contract scripts check TLS, HTTPS access to the Go proxy, required tools,
UTF-8 locale, C compilation, Docker/Buildx/Compose versions, and (when the
socket is explicitly mounted) a build-and-remove smoke image.

Pull requests run entirely on GitHub-hosted runners. A protected `main`
release, weekly rebuild (`17 19 * * 0`), or authorized manual dispatch follows
this sequence:

1. A GitHub-hosted candidate job builds both images, scans them, publishes
   `sha-<7-char-source-sha>` candidates to GHCR, and emits provenance and
   CycloneDX SBOM attestations.
2. Two read-only `lab-small` jobs consume those immutable candidates. The base
   smoke repeats the base contract; the Docker smoke mounts the socket
   explicitly and repeats both contracts.
3. A dependent GitHub-hosted promotion job, and only that job, has package write
   permission. It creates the dated tag if absent and moves `:24.04` to the
   smoke-tested candidate digest.

Lab jobs have only `contents: read` and `packages: read`; they never log in to
GHCR for publishing and never run fork-controlled revisions. The standard
container limits are 1.5 CPUs, 2560 MiB, 768 PIDs, and
`docker-workloads-ci.slice`.

## Attestation and package checks

Verify the published provenance or SBOM attestation for a digest with the
GitHub CLI after replacing the digest:

```bash
gh attestation verify \
  oci://ghcr.io/kitae0522/ci-ubuntu-base@sha256:<digest> \
  -R kitae0522/ci-images
```

Check both image names and both digests before adoption. After publication,
verify that packages are publicly pullable without credentials from a clean
Docker configuration:

```bash
tmp_config="$(mktemp -d)"
DOCKER_CONFIG="$tmp_config" docker manifest inspect \
  ghcr.io/kitae0522/ci-ubuntu-base:24.04
DOCKER_CONFIG="$tmp_config" docker manifest inspect \
  ghcr.io/kitae0522/ci-ubuntu-docker:24.04
rm -rf "$tmp_config"
```

## Updates and vulnerability policy

Dependabot checks both Dockerfiles and GitHub Actions weekly. The base image
rebuilds weekly so refreshed Ubuntu packages are included. A fixed `CRITICAL`
vulnerability fails validation and blocks candidate promotion. `HIGH` findings
are reported; promotion requires an explicit documented exception when no
fixed package is available. Unfixed findings do not silently become release
approval.

See [SECURITY.md](SECURITY.md) for the runner and Docker-socket boundary and
private vulnerability reporting.

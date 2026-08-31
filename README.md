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
  `ninja-build`, `python3`, and `file`;
- Chrome/Chromium shared libraries (`libglib`, `nss`, `gbm`, and related
  X11 packages) plus Liberation fonts, so a job-downloaded browser can start.

`ci-ubuntu-docker` extends the base image with `docker-ce-cli`, Buildx, and
Compose. It contains Docker client tools only, not a Docker daemon.

The images deliberately exclude Go, Node.js, Rust, Java, Chrome/Chromium
binaries, project database clients, cloud CLIs, credentials, private source,
SSH keys, Nix secrets, and a Docker daemon. Workflows choose language versions
with `setup-*` actions and download a browser only when a job needs one.

The upstream Actions runner automatically mounts `/var/run/docker.sock` into
container jobs. This repository relocates the real daemon socket, so that
automatic mount is intentionally inert: lab base-image smoke fails when the
path is a usable socket, and workflows must not add a host mapping for it. A
Docker-enabled job opts in through the alternate `/run/lab-docker/docker.sock`
path and sets `DOCKER_HOST` to that same path. That opt-in still gives the job
effective root control of the Docker host, so it is limited to trusted private
workflows.

## Closed workflow runner contracts

The policy tool at `tools/workflow-policy` inventories every workflow and
template. It fails closed for an unknown or missing file/job, so adding a new
lab job requires an intentional policy change and review. The inventory is:

- Hosted jobs (`validate`, `candidate`, and `promote`) run only on
  `ubuntu-24.04` and have no job container or services.
- This repository no longer runs jobs on the lab self-hosted pool. Lab
  contracts remain only in the reusable templates that private consumers copy.
- Lab-base templates (`templates/go.yml`) use the exact
  `self-hosted, lab, linux, x64, container, lab-small` labels, the
  allowlisted base image, and the standard 1.5 CPU / 2560 MiB / 768 PID /
  `docker-workloads-ci.slice` options. They have no services, volumes, or
  container environment, and their first step must be exactly
  `test ! -S /var/run/docker.sock` with no execution override.
- Lab-docker templates (`templates/docker-build.yml`) use the same labels and
  resource options, the allowlisted Docker image, exactly
  `DOCKER_HOST=unix:///run/lab-docker/docker.sock`, and exactly one
  `/run/lab-docker/docker.sock:/run/lab-docker/docker.sock` mapping. Services,
  default-socket mappings, extra host mounts, and dynamic policy values are
  rejected.

The hosted validation job runs the pinned Go policy module and actionlint:

```bash
(cd tools/workflow-policy && \
  go mod verify && go test ./... && go run . ../.. && \
  go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 \
    ../../.github/workflows/*.yml)
```

## Tags and reproducibility

- `:candidate-<sha7>-<run_id>-<run_attempt>` is the immutable, run-unique
  candidate tag. It includes the source SHA, `GITHUB_RUN_ID`, and
  `GITHUB_RUN_ATTEMPT`.
- `:24.04-YYYYMMDD` is an immutable dated release and is never overwritten.
- `:24.04` is the rolling stable tag, moved after the hosted candidate
  publishes and the promotion job retags the candidate digest.
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

Run the closed workflow policy gate as well (the CI job pins Go 1.25 and
actionlint v1.7.12):

```bash
(cd tools/workflow-policy && \
  go mod verify && go test ./... && go run . ../.. && \
  go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 \
    ../../.github/workflows/*.yml)
```

The contract scripts check TLS, HTTPS access to the Go proxy, required tools,
UTF-8 locale, C compilation, Docker/Buildx/Compose versions, and (when the
socket is explicitly mounted) a build-and-remove smoke image.

Pull requests run entirely on GitHub-hosted runners. A protected `main`
release, weekly rebuild (`17 19 * * 0`), or authorized manual dispatch follows
this sequence:

1. A GitHub-hosted candidate job builds both images and publishes the
   run-unique tag `candidate-<sha7>-<run_id>-<run_attempt>` to GHCR. It
   emits the exact base and Docker manifest digests plus provenance and
   CycloneDX SBOM attestations.
2. `HIGH` findings are scanned and reported for both images. Push and
   scheduled runs fail closed when either scan finds one. A manual dispatch may
   continue only when its non-empty `high_vulnerability_exception` input
   documents an owner, justification, and expiration; that exception is copied
   to the workflow summary.
3. The GitHub-hosted promotion job creates the dated tag if absent and moves
   `:24.04` to the candidate digest. Lab no longer smokes these images.

Only the hosted `candidate` and `promote` jobs have `packages: write` (the
candidate also has attestation permissions). Lab jobs have only
`contents: read` and `packages: read`; they never log in to GHCR for
publishing and never run fork-controlled revisions. The standard container
limits are 1.5 CPUs, 2560 MiB, 768 PIDs, and `docker-workloads-ci.slice`.

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
vulnerability fails validation and blocks candidate promotion. Both images are
scanned for `HIGH` findings; push and scheduled releases fail closed when either
scan reports one. Only an authorized `workflow_dispatch` with a
non-empty, documented `high_vulnerability_exception` (owner, justification,
and expiration) may continue, and the exception is recorded in the workflow
summary.

See [SECURITY.md](SECURITY.md) for the runner and Docker-socket boundary and
private vulnerability reporting.

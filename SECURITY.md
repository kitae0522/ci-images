# Security policy

## Runner trust boundary

Candidate references use the immutable format
`candidate-<sha7>-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`; lab smoke consumes the
published digest rather than a tag.

The `lab` runner is reserved for trusted, protected-repository revisions. Fork-
controlled code must never select the `lab` labels, and the public
`ci-images` repository never runs `pull_request` or `pull_request_target` code on
`lab`. Pull-request validation stays on GitHub-hosted runners. Lab smoke runs
only after a run-unique immutable candidate is published from protected `main`
(or an authorized manual dispatch) and has package read permission only. The
hosted `candidate` and `promote` jobs are the only jobs with `packages: write`;
lab smoke jobs remain limited to `contents: read` and `packages: read`.

## Docker socket

The upstream Actions runner automatically mounts `/var/run/docker.sock` into
container jobs. The real daemon socket is relocated, making that automatic
mount intentionally inert. The default path is not an authorization
mechanism: base-image lab smoke explicitly fails if it is a usable socket, and
this repository rejects workflow or template mappings to that path.

Docker-enabled jobs must opt in with the alternate host socket at
`/run/lab-docker/docker.sock` and set `DOCKER_HOST` to
`unix:///run/lab-docker/docker.sock`. The Docker image contains client tools
only, not a daemon. An opted-in job still has effective root control of the
Docker host, so only trusted private workflows may use the socket-enabled
template. Ordinary workflows must use the base image and must not add either
socket mapping.

## Image contents and releases

Images contain no credentials, private source, tokens, SSH keys, Nix secrets,
or Docker daemon. Candidates are scanned for fixed critical vulnerabilities;
fixed critical findings block promotion. Both images are scanned for `HIGH`
findings and those findings block push and scheduled releases. Only an
authorized `workflow_dispatch` with a non-empty, documented
`high_vulnerability_exception` (owner, justification, and expiration) may
continue; the exception is recorded in the workflow summary. Verify provenance
and SBOM attestations before adopting an image, and prefer the exact smoke-tested
digest or an immutable date tag for reproducibility.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting for this repository:
<https://github.com/kitae0522/ci-images/security/advisories/new>. Do not include
secrets or sensitive exploit details in public issues or pull requests.

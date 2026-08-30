# Security policy

## Runner trust boundary

The `lab` runner is reserved for trusted, protected-repository revisions. Fork-
controlled code must never select the `lab` labels, and the public
`ci-images` repository never runs `pull_request` or `pull_request_target` code on
`lab`. Pull-request validation stays on GitHub-hosted runners. Lab smoke runs
only after an immutable candidate is published from protected `main` (or an
authorized manual dispatch) and has package read permission only.

## Docker socket

Mounting `/var/run/docker.sock` grants the job effective root access to the
Docker host. The Docker image does not include a daemon; only trusted private
workflows may use the socket-mounted template. Ordinary workflows must use the
base image and must not add the socket.

## Image contents and releases

Images contain no credentials, private source, tokens, SSH keys, Nix secrets,
or Docker daemon. Candidates are scanned for fixed critical vulnerabilities;
fixed critical findings block promotion. High findings are reported and need a
documented exception when no fixed package is available. Verify provenance and
SBOM attestations before adopting an image, and prefer a digest or immutable
date tag for reproducibility.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting for this repository:
<https://github.com/kitae0522/ci-images/security/advisories/new>. Do not include
secrets or sensitive exploit details in public issues or pull requests.

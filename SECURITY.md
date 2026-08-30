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

Mounting `/var/run/docker.sock` grants the job effective root access to the
Docker host. The Docker image does not include a daemon; only trusted private
workflows may use the socket-mounted template. Ordinary workflows must use the
base image and must not add the socket.

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

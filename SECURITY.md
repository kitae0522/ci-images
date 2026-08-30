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
mechanism: every lab-base job starts with an exact guard,
`test ! -S /var/run/docker.sock`, and the closed workflow policy rejects
workflow or template mappings to either `/var/run/docker.sock` or
`/run/docker.sock`.

The policy tool has a closed inventory of runner contracts. Hosted
`validate`, `candidate`, and `promote` jobs run only on `ubuntu-24.04` and have
no container or services. Lab-base `smoke-base`, `hold`, and the Go template's
`test` use the exact `self-hosted, lab, linux, x64, container, lab-small`
labels, the allowlisted base image, and only the standard CPU, memory, PID,
and cgroup options. They have no services, volumes, or container environment;
the inert-socket guard must be the first step and may not be made skippable or
override its shell.

Docker-enabled `smoke-docker` and Docker-template `test` jobs must use the
same labels and resource options, the allowlisted Docker image, exactly one
mapping of `/run/lab-docker/docker.sock` to the same path, and exactly
`DOCKER_HOST=unix:///run/lab-docker/docker.sock`. Services, default-socket or
other host mounts, privileged/PID-host options, dynamic policy fields, and
unknown lab jobs are rejected. The Docker image contains client tools only,
not a daemon. An opted-in job still has effective root control of the Docker
host, so only trusted private workflows may use the socket-enabled template.
Ordinary workflows must use the base image and must not add either socket
mapping.

Adding a workflow or lab job requires updating the allowlist and passing the
pinned Go/actionlint policy gate; an unlisted or missing file/job fails closed.

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

#!/usr/bin/env python3
"""Enforce explicit, opt-in Docker socket access in lab workflows."""

from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
CAPACITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lab-capacity-smoke.yml"
DOCKER_TEMPLATE = REPO_ROOT / "templates" / "docker-build.yml"

DEFAULT_SOCKET_MAPPING = re.compile(r"/var/run/docker\.sock\s*:")
ALTERNATE_SOCKET = "/run/lab-docker/docker.sock"
INERT_SOCKET_CHECK = "test ! -S /var/run/docker.sock"


def job_block(workflow: str, job_name: str) -> str:
    """Return one top-level job block from a GitHub Actions workflow."""
    match = re.search(rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>(?:^(?!  \w)[^\n]*(?:\n|$))*)", workflow)
    if match is None:
        return ""
    return match.group("body")


def socket_policy_failures() -> list[str]:
    failures: list[str] = []
    workflow_root = REPO_ROOT / ".github" / "workflows"
    policy_files = sorted((*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml"), DOCKER_TEMPLATE))

    for path in policy_files:
        if DEFAULT_SOCKET_MAPPING.search(path.read_text()):
            failures.append(f"{path}: host-side /var/run/docker.sock mapping is forbidden")

    publish = PUBLISH_WORKFLOW.read_text()
    smoke_base = job_block(publish, "smoke-base")
    if INERT_SOCKET_CHECK not in smoke_base:
        failures.append(f"{PUBLISH_WORKFLOW}: smoke-base must assert the default socket is inert")
    if smoke_base and INERT_SOCKET_CHECK in smoke_base:
        check_position = smoke_base.index(INERT_SOCKET_CHECK)
        meaningful_position = smoke_base.find("actions/checkout@")
        if meaningful_position < 0:
            meaningful_position = smoke_base.find("tests/base-contract.sh")
        if meaningful_position >= 0 and check_position > meaningful_position:
            failures.append(f"{PUBLISH_WORKFLOW}: smoke-base socket check must precede meaningful work")

    capacity = CAPACITY_WORKFLOW.read_text()
    hold = job_block(capacity, "hold")
    if INERT_SOCKET_CHECK not in hold:
        failures.append(f"{CAPACITY_WORKFLOW}: hold must assert the default socket is inert")
    if hold and INERT_SOCKET_CHECK in hold:
        check_position = hold.index(INERT_SOCKET_CHECK)
        meaningful_position = hold.find("test -s /etc/ssl/certs/ca-certificates.crt")
        if meaningful_position >= 0 and check_position > meaningful_position:
            failures.append(f"{CAPACITY_WORKFLOW}: hold socket check must precede the image contract")

    docker_jobs = {
        PUBLISH_WORKFLOW: job_block(publish, "smoke-docker"),
        DOCKER_TEMPLATE: job_block(DOCKER_TEMPLATE.read_text(), "test"),
    }
    for path, content in docker_jobs.items():
        if f"DOCKER_HOST: unix://{ALTERNATE_SOCKET}" not in content:
            failures.append(f"{path}: Docker job must set the alternate DOCKER_HOST")
        if f"- {ALTERNATE_SOCKET}:{ALTERNATE_SOCKET}" not in content:
            failures.append(f"{path}: Docker job must mount only the alternate socket at the same path")

    return failures


def main() -> int:
    failures = socket_policy_failures()
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print("validated explicit opt-in Docker socket policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

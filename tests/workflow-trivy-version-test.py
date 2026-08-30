#!/usr/bin/env python3
"""Require every Trivy workflow step to pin one shared explicit version."""

from pathlib import Path
import re
import sys


TRIVY_ACTION = re.compile(r"uses:\s+aquasecurity/trivy-action@[0-9a-f]{40}\b")
TRIVY_VERSION = re.compile(r"(?m)^\s+version:\s+(v\d+\.\d+\.\d+)\s*$")
STEP_START = re.compile(r"(?m)^      - ")
EXPECTED_TRIVY_VERSION = "v0.74.0"
EXPECTED_TRIVY_STEPS = 9


def workflow_steps(workflow: Path) -> list[str]:
    return STEP_START.split(workflow.read_text())[1:]


def main() -> int:
    versions: set[str] = set()
    failures: list[str] = []
    count = 0

    workflow_root = Path(".github/workflows")
    workflows = sorted((*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml")))
    for workflow in workflows:
        for index, step in enumerate(workflow_steps(workflow), start=1):
            if "aquasecurity/trivy-action@" not in step:
                continue
            count += 1
            if not TRIVY_ACTION.search(step):
                failures.append(f"{workflow}: Trivy step {index} is not pinned to a full commit SHA")
            match = TRIVY_VERSION.search(step)
            if match is None:
                failures.append(f"{workflow}: Trivy step {index} has no explicit version")
            else:
                versions.add(match.group(1))

    if count != EXPECTED_TRIVY_STEPS:
        failures.append(f"expected {EXPECTED_TRIVY_STEPS} Trivy steps, found {count}")
    if versions != {EXPECTED_TRIVY_VERSION}:
        found = ", ".join(sorted(versions)) or "none"
        failures.append(f"expected only Trivy {EXPECTED_TRIVY_VERSION}, found: {found}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"validated {count} Trivy steps at {next(iter(versions))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

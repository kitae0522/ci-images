#!/usr/bin/env python3
"""Negative cases for the Trivy workflow policy checker."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tests" / "workflow-trivy-version-test.py"
ACTION_SHA = "ed142fd0673e97e23eac54620cfb913e5ce36c25"


def workflow(version: str, count: int) -> str:
    steps = "\n".join(
        f"""      - uses: aquasecurity/trivy-action@{ACTION_SHA}
        with:
          version: {version}
          image-ref: image-{index}:test"""
        for index in range(count)
    )
    return f"""name: fixture
jobs:
  validate:
    steps:
{steps}
"""


def run_checker(version: str, count: int, suffix: str = ".yml") -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workflows = root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / f"fixture{suffix}").write_text(workflow(version, count))
        return subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )


class WorkflowTrivyVersionCheckerTest(unittest.TestCase):
    def test_rejects_removed_trivy_version(self) -> None:
        self.assertNotEqual(run_checker("v0.65.0", 9).returncode, 0)

    def test_rejects_missing_trivy_step(self) -> None:
        self.assertNotEqual(run_checker("v0.74.0", 8).returncode, 0)

    def test_accepts_yaml_workflow_extension(self) -> None:
        self.assertEqual(run_checker("v0.74.0", 9, ".yaml").returncode, 0)


if __name__ == "__main__":
    unittest.main()

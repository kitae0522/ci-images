#!/usr/bin/env python3
"""Enforce explicit, opt-in Docker socket access in lab workflows."""

from pathlib import Path
import json
import shlex
import sys
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
CAPACITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lab-capacity-smoke.yml"
DOCKER_TEMPLATE = REPO_ROOT / "templates" / "docker-build.yml"

DEFAULT_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")
ALTERNATE_SOCKET = "/run/lab-docker/docker.sock"
ALTERNATE_SOCKET_MAPPING = f"{ALTERNATE_SOCKET}:{ALTERNATE_SOCKET}"
INERT_SOCKET_CHECK = "test ! -S /var/run/docker.sock"
DOCKER_HOST = f"unix://{ALTERNATE_SOCKET}"


class YamlParseError(ValueError):
    """Raised when the small YAML subset used by workflow files is invalid."""


def _strip_inline_comment(value: str) -> str:
    """Remove a YAML comment without changing quoted scalar values."""
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in ("'", '"'):
            quote = character
        elif character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _find_mapping_colon(value: str) -> int:
    """Find the key/value colon in a plain or quoted YAML mapping entry."""
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in ("'", '"'):
            quote = character
        elif character == ":" and (index + 1 == len(value) or value[index + 1].isspace()):
            return index
    return -1


def _split_flow_items(value: str) -> list[str]:
    """Split a simple YAML flow sequence while respecting quotes and nesting."""
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in ("'", '"'):
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == "," and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
    final = value[start:].strip()
    if final:
        items.append(final)
    return items


def _parse_scalar(value: str) -> Any:
    value = _strip_inline_comment(value).strip()
    if value == "":
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [_parse_scalar(item) for item in _split_flow_items(inner)]
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, Any] = {}
        inner = value[1:-1].strip()
        if not inner:
            return result
        for item in _split_flow_items(inner):
            colon = _find_mapping_colon(item)
            if colon < 0:
                raise YamlParseError(f"invalid flow mapping item: {item!r}")
            result[str(_parse_scalar(item[:colon]))] = _parse_scalar(item[colon + 1 :])
        return result
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1].replace("''", "'")
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    return value


class _YamlParser:
    """Parse the indentation-based YAML subset used by GitHub workflows.

    This deliberately stays dependency-free so the hosted validation job does
    not need to install an unpinned YAML package. It supports mappings,
    sequences, quoted/flow scalars, comments, and literal/folded block values,
    which covers the workflow syntax checked by this policy.
    """

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.index = 0

    def parse(self) -> Any:
        self._skip_ignorable()
        if self.index >= len(self.lines):
            return None
        indent = self._line_indent(self.index)
        value = self._parse_block(indent)
        self._skip_ignorable()
        if self.index < len(self.lines):
            raise YamlParseError(f"unexpected content at line {self.index + 1}")
        return value

    def _skip_ignorable(self) -> None:
        while self.index < len(self.lines):
            stripped = self.lines[self.index].strip()
            if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
                self.index += 1
                continue
            break

    def _line_indent(self, index: int) -> int:
        line = self.lines[index]
        if "\t" in line[: len(line) - len(line.lstrip(" "))]:
            raise YamlParseError(f"tab indentation at line {index + 1}")
        return len(line) - len(line.lstrip(" "))

    def _line_content(self, index: int) -> str:
        indent = self._line_indent(index)
        return _strip_inline_comment(self.lines[index][indent:]).strip()

    def _parse_block(self, indent: int) -> Any:
        self._skip_ignorable()
        if self.index >= len(self.lines):
            return None
        actual_indent = self._line_indent(self.index)
        if actual_indent != indent:
            raise YamlParseError(
                f"expected indentation {indent}, got {actual_indent} at line {self.index + 1}"
            )
        content = self._line_content(self.index)
        if content == "-" or content.startswith("- "):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines):
                break
            actual_indent = self._line_indent(self.index)
            content = self._line_content(self.index)
            if actual_indent != indent or content == "-" or content.startswith("- "):
                break
            colon = _find_mapping_colon(content)
            if colon < 0:
                raise YamlParseError(f"expected mapping entry at line {self.index + 1}")
            key = _parse_scalar(content[:colon])
            if not isinstance(key, str):
                key = str(key)
            raw_value = content[colon + 1 :].strip()
            self.index += 1
            result[key] = self._parse_value(raw_value, indent)
        return result

    def _parse_sequence(self, indent: int) -> list[Any]:
        result: list[Any] = []
        while True:
            self._skip_ignorable()
            if self.index >= len(self.lines):
                break
            actual_indent = self._line_indent(self.index)
            content = self._line_content(self.index)
            if actual_indent != indent or not (content == "-" or content.startswith("- ")):
                break
            raw_item = content[1:].lstrip()
            self.index += 1
            if not raw_item:
                self._skip_ignorable()
                if self.index < len(self.lines) and self._line_indent(self.index) > indent:
                    result.append(self._parse_block(self._line_indent(self.index)))
                else:
                    result.append(None)
                continue
            colon = _find_mapping_colon(raw_item)
            if colon < 0:
                result.append(_parse_scalar(raw_item))
                continue

            key = _parse_scalar(raw_item[:colon])
            if not isinstance(key, str):
                key = str(key)
            item: dict[str, Any] = {key: self._parse_value(raw_item[colon + 1 :].strip(), indent)}

            self._skip_ignorable()
            if self.index < len(self.lines) and self._line_indent(self.index) > indent:
                nested = self._parse_block(self._line_indent(self.index))
                if not isinstance(nested, dict):
                    raise YamlParseError(f"sequence mapping has non-mapping continuation at line {self.index + 1}")
                item.update(nested)
            result.append(item)
        return result

    def _parse_value(self, raw_value: str, parent_indent: int) -> Any:
        if raw_value.startswith(("|", ">")):
            return self._parse_block_scalar(raw_value, parent_indent)
        if raw_value:
            return _parse_scalar(raw_value)
        self._skip_ignorable()
        if self.index < len(self.lines) and self._line_indent(self.index) > parent_indent:
            return self._parse_block(self._line_indent(self.index))
        return None

    def _parse_block_scalar(self, indicator: str, parent_indent: int) -> str:
        style = indicator[0]
        chomping = indicator[1:]
        start = self.index
        block_indent: int | None = None
        cursor = self.index
        while cursor < len(self.lines):
            line = self.lines[cursor]
            if not line.strip():
                cursor += 1
                continue
            current_indent = self._line_indent(cursor)
            if current_indent <= parent_indent:
                break
            block_indent = current_indent if block_indent is None else min(block_indent, current_indent)
            cursor += 1

        if block_indent is None:
            self.index = cursor
            return ""

        values: list[str] = []
        for line in self.lines[start:cursor]:
            if not line.strip():
                values.append("")
            else:
                values.append(line[block_indent:])
        self.index = cursor
        if style == ">":
            folded: list[str] = []
            for value in values:
                if folded and value and folded[-1]:
                    folded[-1] += " " + value
                else:
                    folded.append(value)
            result = "\n".join(folded)
        else:
            result = "\n".join(values)
        if chomping != "-":
            result += "\n"
        return result


def parse_workflow(text: str) -> Any:
    """Parse a workflow document without relying on third-party packages."""
    return _YamlParser(text).parse()


def _jobs(document: Any, path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(document, dict):
        return None, [f"{path}: workflow root must be a mapping"]
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return None, [f"{path}: workflow must define a jobs mapping"]
    return jobs, []


def _job(document: Any, path: Path, job_name: str) -> tuple[dict[str, Any] | None, list[str]]:
    jobs, failures = _jobs(document, path)
    if failures:
        return None, failures
    assert jobs is not None
    value = jobs.get(job_name)
    if not isinstance(value, dict):
        return None, [f"{path}: missing jobs.{job_name} mapping"]
    return value, []


def _base_guard_failures(document: Any, path: Path, job_name: str) -> list[str]:
    job, failures = _job(document, path, job_name)
    if failures:
        return failures
    assert job is not None
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        return [f"{path}: jobs.{job_name}.steps must contain the inert-socket guard first"]
    first = steps[0]
    actual = first.get("run") if isinstance(first, dict) else None
    if actual != INERT_SOCKET_CHECK:
        return [f"{path}: jobs.{job_name}.steps[0].run must be {INERT_SOCKET_CHECK!r}"]
    if "continue-on-error" in first and first["continue-on-error"] is not False:
        failures.append(
            f"{path}: jobs.{job_name}.steps[0].continue-on-error must be the literal false"
        )
    if "if" in first:
        failures.append(f"{path}: jobs.{job_name}.steps[0].if must not make the guard skippable")
    return failures


def _docker_contract_failures(document: Any, path: Path, job_name: str) -> list[str]:
    job, failures = _job(document, path, job_name)
    if failures:
        return failures
    assert job is not None
    container = job.get("container")
    if not isinstance(container, dict):
        return [f"{path}: jobs.{job_name}.container must be a mapping"]

    env = container.get("env")
    actual_host = env.get("DOCKER_HOST") if isinstance(env, dict) else None
    if actual_host != DOCKER_HOST:
        failures.append(f"{path}: jobs.{job_name}.container.env.DOCKER_HOST must be {DOCKER_HOST!r}")

    volumes = container.get("volumes")
    if not isinstance(volumes, list) or ALTERNATE_SOCKET_MAPPING not in volumes:
        failures.append(
            f"{path}: jobs.{job_name}.container.volumes must contain {ALTERNATE_SOCKET_MAPPING!r}"
        )
    return failures


def _default_socket_mapping(value: Any) -> bool:
    return isinstance(value, str) and value.split(":", 1)[0].strip() in DEFAULT_SOCKETS


def _default_socket_in_options(options: Any) -> tuple[bool, str | None]:
    if options is None:
        return False, None
    if not isinstance(options, str):
        return False, "options must be a string"
    try:
        tokens = shlex.split(options)
    except ValueError as error:
        return False, str(error)

    for index, token in enumerate(tokens):
        value: str | None = None
        option = token
        if token in {"--volume", "-v", "--mount"}:
            if index + 1 < len(tokens):
                value = tokens[index + 1]
        elif token.startswith(("--volume=", "-v=", "--mount=")):
            option, value = token.split("=", 1)

        if value is None:
            continue
        if option in {"--volume", "-v"} and _default_socket_mapping(value):
            return True, None
        if option == "--mount":
            for field in value.split(","):
                key, separator, field_value = field.partition("=")
                if separator and key in {"src", "source"} and field_value.strip() in DEFAULT_SOCKETS:
                    return True, None
    return False, None


def _default_socket_mapping_failures(document: Any, path: Path) -> list[str]:
    jobs, failures = _jobs(document, path)
    if failures:
        return failures
    assert jobs is not None
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        container = job.get("container")
        if isinstance(container, dict):
            volumes = container.get("volumes")
            if isinstance(volumes, list):
                for volume in volumes:
                    if _default_socket_mapping(volume):
                        failures.append(f"{path}: jobs.{job_name}.container.volumes maps the host default Docker socket")
            found, parse_error = _default_socket_in_options(container.get("options"))
            if parse_error:
                failures.append(f"{path}: jobs.{job_name}.container.options is invalid: {parse_error}")
            elif found:
                failures.append(f"{path}: jobs.{job_name}.container.options maps the host default Docker socket")

        services = job.get("services")
        if not isinstance(services, dict):
            continue
        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            service_volumes = service.get("volumes")
            if isinstance(service_volumes, list):
                for volume in service_volumes:
                    if _default_socket_mapping(volume):
                        failures.append(
                            f"{path}: jobs.{job_name}.services.{service_name}.volumes maps the host default Docker socket"
                        )
            found, parse_error = _default_socket_in_options(service.get("options"))
            if parse_error:
                failures.append(
                    f"{path}: jobs.{job_name}.services.{service_name}.options is invalid: {parse_error}"
                )
            elif found:
                failures.append(
                    f"{path}: jobs.{job_name}.services.{service_name}.options maps the host default Docker socket"
                )
    return failures


def _negative_fixture_failures() -> list[str]:
    """Ensure lookalikes, socket aliases, and skippable guards cannot pass."""
    fixtures: list[tuple[str, str, Callable[[Any, Path], list[str]]]] = [
        (
            "comment-only base guard",
            """
jobs:
  smoke-base:
    steps:
      # test ! -S /var/run/docker.sock
      - uses: actions/checkout@v6
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "wrong step and run block",
            """
jobs:
  smoke-base:
    steps:
      - name: Lookalike
        run: |
          test ! -S /var/run/docker.sock
          echo not-the-guard
      - run: test ! -S /var/run/docker.sock
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "wrong Docker host env placement",
            """
jobs:
  test:
    env:
      DOCKER_HOST: unix:///run/lab-docker/docker.sock
    container:
      image: ci-ubuntu-docker:24.04
      volumes:
        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
""",
            lambda document, path: _docker_contract_failures(document, path, "test"),
        ),
        (
            "wrong Docker volume placement",
            """
jobs:
  test:
    container:
      image: ci-ubuntu-docker:24.04
      env:
        DOCKER_HOST: unix:///run/lab-docker/docker.sock
    volumes:
      - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
""",
            lambda document, path: _docker_contract_failures(document, path, "test"),
        ),
        (
            "default socket in container --volume option",
            """
name: socket option fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --volume /var/run/docker.sock:/var/run/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "default socket in container --mount option",
            """
name: socket mount fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --mount type=bind,src=/run/docker.sock,dst=/run/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "default socket in service volumes",
            """
name: service volume fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        volumes:
          - /var/run/docker.sock:/var/run/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "default socket in service --mount option",
            """
name: service mount fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        options: --mount type=bind,source=/run/docker.sock,target=/run/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "continue-on-error true on guard",
            """
jobs:
  smoke-base:
    steps:
      - run: test ! -S /var/run/docker.sock
        continue-on-error: true
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "dynamic continue-on-error on guard",
            """
on:
  workflow_dispatch:
    inputs:
      allow_failure:
        required: false
        type: boolean
jobs:
  smoke-base:
    steps:
      - run: test ! -S /var/run/docker.sock
        continue-on-error: ${{ inputs.allow_failure }}
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "conditional guard",
            """
jobs:
  smoke-base:
    steps:
      - run: test ! -S /var/run/docker.sock
        if: ${{ always() }}
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
    ]

    failures: list[str] = []
    fixture_path = Path("<negative-fixture>")
    for name, text, checker in fixtures:
        try:
            document = parse_workflow(text)
            rejected = checker(document, fixture_path)
        except (TypeError, YamlParseError, ValueError) as error:
            failures.append(f"negative fixture {name!r} could not be parsed: {error}")
            continue
        if not rejected:
            failures.append(f"negative fixture {name!r} was unexpectedly accepted")

    safe_fixtures = [
        (
            "alternate socket options and service values",
            """
name: safe socket fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: >-
        --volume /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
        --mount type=bind,src=/run/lab-docker/docker.sock,dst=/run/lab-docker/docker.sock
    services:
      docker:
        image: docker:27
        volumes:
          - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
        options: --mount type=bind,source=/run/lab-docker/docker.sock,target=/run/lab-docker/docker.sock
    steps:
      - run: echo test
""",
            _default_socket_mapping_failures,
        ),
        (
            "literal false guard",
            """
jobs:
  smoke-base:
    steps:
      - run: test ! -S /var/run/docker.sock
        continue-on-error: false
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
    ]
    for name, text, checker in safe_fixtures:
        try:
            document = parse_workflow(text)
            accepted = checker(document, fixture_path)
        except (TypeError, YamlParseError, ValueError) as error:
            failures.append(f"safe fixture {name!r} could not be parsed: {error}")
            continue
        if accepted:
            failures.append(f"safe fixture {name!r} was unexpectedly rejected: {accepted}")
    return failures


def socket_policy_failures() -> list[str]:
    failures = _negative_fixture_failures()
    workflow_root = REPO_ROOT / ".github" / "workflows"
    policy_files = sorted((*workflow_root.glob("*.yml"), *workflow_root.glob("*.yaml"), DOCKER_TEMPLATE))
    documents: dict[Path, Any] = {}

    for path in policy_files:
        try:
            document = parse_workflow(path.read_text())
        except (OSError, YamlParseError, ValueError) as error:
            failures.append(f"{path}: unable to parse workflow YAML: {error}")
            continue
        documents[path] = document
        failures.extend(_default_socket_mapping_failures(document, path))

    publish = documents.get(PUBLISH_WORKFLOW)
    if publish is not None:
        failures.extend(_base_guard_failures(publish, PUBLISH_WORKFLOW, "smoke-base"))
        failures.extend(_docker_contract_failures(publish, PUBLISH_WORKFLOW, "smoke-docker"))

    capacity = documents.get(CAPACITY_WORKFLOW)
    if capacity is not None:
        failures.extend(_base_guard_failures(capacity, CAPACITY_WORKFLOW, "hold"))

    template = documents.get(DOCKER_TEMPLATE)
    if template is not None:
        failures.extend(_docker_contract_failures(template, DOCKER_TEMPLATE, "test"))

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

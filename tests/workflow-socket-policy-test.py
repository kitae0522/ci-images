#!/usr/bin/env python3
"""Enforce explicit, opt-in Docker socket access in lab workflows."""

from pathlib import Path
import json
import posixpath
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
MOUNT_FIELDS = {
    "type",
    "src",
    "source",
    "dst",
    "destination",
    "target",
    "readonly",
    "ro",
    "volume-subpath",
    "volume-nocopy",
    "bind-propagation",
    "consistency",
    "tmpfs-size",
    "tmpfs-mode",
    "tmpfs-options",
}
MOUNT_FLAG_FIELDS = {"readonly", "ro", "volume-nocopy"}


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
    if value.lstrip("-").isdigit():
        return int(value)
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
    if "shell" in first:
        failures.append(f"{path}: jobs.{job_name}.steps[0].shell must not override the guard shell")
    if "if" in job:
        failures.append(f"{path}: jobs.{job_name}.if must not make the guard job skippable")
    if "continue-on-error" in job:
        failures.append(f"{path}: jobs.{job_name}.continue-on-error must not override the guard")
    defaults = job.get("defaults")
    if isinstance(defaults, dict):
        run_defaults = defaults.get("run")
        if isinstance(run_defaults, dict) and "shell" in run_defaults:
            failures.append(f"{path}: jobs.{job_name}.defaults.run.shell must not override the guard shell")
        elif run_defaults is not None and not isinstance(run_defaults, dict):
            failures.append(f"{path}: jobs.{job_name}.defaults.run must be absent for the guard")
    elif defaults is not None:
        failures.append(f"{path}: jobs.{job_name}.defaults must not override the guard shell")
    workflow_defaults = document.get("defaults") if isinstance(document, dict) else None
    if isinstance(document, dict) and "defaults" in document:
        if not isinstance(workflow_defaults, dict):
            failures.append(f"{path}: workflow defaults must be a mapping for the guard")
        elif "run" in workflow_defaults:
            workflow_run_defaults = workflow_defaults["run"]
            if not isinstance(workflow_run_defaults, dict):
                failures.append(f"{path}: workflow defaults.run must be a mapping for the guard")
            elif "shell" in workflow_run_defaults:
                failures.append(f"{path}: workflow defaults.run.shell must not override the guard shell")
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
    if not isinstance(value, str):
        return False
    if ":" not in value:
        return False
    source = value.split(":", 1)[0].strip()
    if source.startswith("/"):
        source = posixpath.normpath("/" + source.lstrip("/"))
    else:
        source = posixpath.normpath(source)
    return source in DEFAULT_SOCKETS


def _dynamic_socket_policy_value(value: Any) -> bool:
    return isinstance(value, str) and ("${{" in value or "$" in value)


def _unsafe_mount_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return "unknown or non-string value"
    if not value.strip():
        return "empty value"
    if _dynamic_socket_policy_value(value):
        return "dynamic expression"
    if value.lstrip().startswith("!"):
        return "YAML tag"
    if "&" in value or "*" in value:
        return "YAML anchor or alias"
    return None


def _volume_mapping_issue(value: Any) -> str | None:
    unsafe = _unsafe_mount_value(value)
    if unsafe:
        return unsafe
    assert isinstance(value, str)
    if ":" not in value:
        return None
    source = value.split(":", 1)[0].strip()
    if not source:
        return "empty host source"
    if not source.startswith("/") and ("/" in source or source in {".", ".."}):
        return "unknown relative host source"
    return None


def _default_socket_in_options(options: Any) -> tuple[bool, str | None]:
    if options is None:
        return False, "options must be a string"
    if not isinstance(options, str):
        return False, "options must be a string"
    unsafe = _unsafe_mount_value(options)
    if unsafe:
        return False, f"options contains an unsafe {unsafe}"
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
            else:
                return False, f"{token} is missing its mount value"
        elif token.startswith(("--volume=", "-v=", "--mount=")):
            option, value = token.split("=", 1)
        elif token.startswith("-v/"):
            option, value = "-v", token[2:]
        elif token.startswith("--volume/"):
            option, value = "--volume", token[len("--volume") :]
        elif token.startswith(("--volume", "--mount", "-v")):
            return False, f"unrecognized mount option {token!r}"

        if value is None:
            continue
        if option in {"--volume", "-v"}:
            issue = _volume_mapping_issue(value)
            if issue:
                return False, f"mount option contains an unsafe {issue}"
            if value.startswith("-"):
                return False, "mount option has an unknown value"
            if _default_socket_mapping(value):
                return True, None
        if option == "--mount":
            unsafe = _unsafe_mount_value(value)
            if unsafe:
                return False, f"mount option contains an unsafe {unsafe}"
            if value.startswith("-"):
                return False, "mount option has an unknown value"
            for field in value.split(","):
                key, separator, field_value = field.partition("=")
                key = key.strip()
                if not separator:
                    if key not in MOUNT_FLAG_FIELDS:
                        return False, f"mount option has an unknown field {key!r}"
                    continue
                if key not in MOUNT_FIELDS:
                    return False, f"mount option has an unknown field {key!r}"
                if key in {"src", "source"}:
                    issue = _volume_mapping_issue(field_value.strip() + ":/target")
                    if issue:
                        return False, f"mount source contains an unsafe {issue}"
                    if _default_socket_mapping(field_value.strip() + ":/target"):
                        return True, None
                elif not field_value.strip() and key not in MOUNT_FLAG_FIELDS:
                    return False, f"mount option field {key!r} has an empty value"
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
        if "container" in job and not isinstance(container, dict):
            failures.append(
                f"{path}: jobs.{job_name}.container contains an unknown or non-mapping value"
            )
        if isinstance(container, dict):
            if "<<" in container:
                failures.append(
                    f"{path}: jobs.{job_name}.container must not use YAML merge aliases"
                )
            if "volumes" in container:
                volumes = container["volumes"]
            else:
                volumes = None
            if isinstance(volumes, list):
                for volume in volumes:
                    issue = _volume_mapping_issue(volume)
                    if issue:
                        failures.append(f"{path}: jobs.{job_name}.container.volumes contains an unsafe {issue}")
                    elif _default_socket_mapping(volume):
                        failures.append(f"{path}: jobs.{job_name}.container.volumes maps the host default Docker socket")
            elif "volumes" in container:
                failures.append(f"{path}: jobs.{job_name}.container.volumes contains an unknown or non-list value")
            if "options" in container:
                found, parse_error = _default_socket_in_options(container["options"])
                if parse_error:
                    failures.append(f"{path}: jobs.{job_name}.container.options is invalid: {parse_error}")
                elif found:
                    failures.append(f"{path}: jobs.{job_name}.container.options maps the host default Docker socket")

        services = job.get("services")
        if "services" in job and not isinstance(services, dict):
            failures.append(
                f"{path}: jobs.{job_name}.services contains an unknown or non-mapping value"
            )
            continue
        if not isinstance(services, dict):
            continue
        for service_name, service in services.items():
            if not isinstance(service, dict):
                failures.append(
                    f"{path}: jobs.{job_name}.services.{service_name} contains an unknown or non-mapping value"
                )
                continue
            if "<<" in service:
                failures.append(
                    f"{path}: jobs.{job_name}.services.{service_name} must not use YAML merge aliases"
                )
            if "volumes" in service:
                service_volumes = service["volumes"]
            else:
                service_volumes = None
            if isinstance(service_volumes, list):
                for volume in service_volumes:
                    issue = _volume_mapping_issue(volume)
                    if issue:
                        failures.append(
                            f"{path}: jobs.{job_name}.services.{service_name}.volumes contains an unsafe {issue}"
                        )
                    elif _default_socket_mapping(volume):
                        failures.append(
                            f"{path}: jobs.{job_name}.services.{service_name}.volumes maps the host default Docker socket"
                        )
            elif "volumes" in service:
                failures.append(
                    f"{path}: jobs.{job_name}.services.{service_name}.volumes contains an unknown or non-list value"
                )
            if "options" in service:
                found, parse_error = _default_socket_in_options(service["options"])
                if parse_error:
                    failures.append(
                        f"{path}: jobs.{job_name}.services.{service_name}.options is invalid: {parse_error}"
                    )
                elif found:
                    failures.append(
                        f"{path}: jobs.{job_name}.services.{service_name}.options maps the host default Docker socket"
                    )
    return failures


def _require_failure_count(document: Any, path: Path, minimum: int) -> list[str]:
    failures = _default_socket_mapping_failures(document, path)
    if len(failures) < minimum:
        return [f"{path}: expected at least {minimum} structural socket failures, got {len(failures)}"]
    return failures


def _negative_fixture_failures() -> list[str]:
    """Ensure lookalikes, socket aliases, and skippable guards cannot pass."""
    fixtures: list[tuple[str, str, Callable[[Any, Path], list[str]]]] = [
        (
            "comment-only base guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    steps:
      # test ! -S /var/run/docker.sock
      - uses: actions/checkout@v6
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "wrong step and run block",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
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
            "default socket in attached -v option",
            """
name: attached volume fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: -v/var/run/docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "default socket alias in attached -v option",
            """
name: attached alias volume fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: -v/run/docker.sock:/mnt/docker.sock
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
            "dynamic container options",
            """
name: dynamic container options fixture
on:
  workflow_dispatch:
    inputs:
      docker_options:
        required: true
        type: string
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: ${{ inputs.docker_options }}
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "dynamic container volume",
            """
name: dynamic container volume fixture
on:
  workflow_dispatch:
    inputs:
      docker_volume:
        required: true
        type: string
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      volumes:
        - ${{ inputs.docker_volume }}
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "dynamic service options",
            """
name: dynamic service options fixture
on:
  workflow_dispatch:
    inputs:
      docker_options:
        required: true
        type: string
jobs:
  test:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        options: ${{ inputs.docker_options }}
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "dynamic service volume",
            """
name: dynamic service volume fixture
on:
  workflow_dispatch:
    inputs:
      docker_volume:
        required: true
        type: string
jobs:
  test:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        volumes:
          - ${{ inputs.docker_volume }}
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "continue-on-error true on guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
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
    runs-on: ubuntu-24.04
    steps:
      - run: test ! -S /var/run/docker.sock
        continue-on-error: ${{ inputs.allow_failure }}
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "conditional guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    steps:
      - run: test ! -S /var/run/docker.sock
        if: ${{ always() }}
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "lexically normalized default volume paths",
            """
name: lexical volume fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      volumes:
        - /run/./docker.sock:/mnt/docker.sock
        - /run//docker.sock:/mnt/docker.sock
        - /run/../run/docker.sock:/mnt/docker.sock
        - /var/run/./docker.sock:/mnt/docker.sock
        - /var/run//docker.sock:/mnt/docker.sock
        - /var/run/../run/docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _require_failure_count(document, path, 6),
        ),
        (
            "lexically normalized default option paths",
            """
name: lexical option fixture
on:
  workflow_dispatch:
jobs:
  long-separated-dot:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --volume /run/./docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
  long-equals-double:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --volume=/run//docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
  short-attached-dot:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: -v/var/run/./docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
  short-equals-double:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: -v=/var/run//docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
  mount-separated-parent:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --mount type=bind,src=/run/../run/docker.sock,dst=/mnt/docker.sock
    steps:
      - run: echo test
  mount-equals-parent:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --mount=type=bind,source=/var/run/../run/docker.sock,target=/mnt/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _require_failure_count(document, path, 6),
        ),
        (
            "lexically normalized service paths",
            """
name: lexical service fixture
on:
  workflow_dispatch:
jobs:
  volume:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        volumes:
          - /run/./docker.sock:/run/docker.sock
          - /var/run//docker.sock:/var/run/docker.sock
    steps:
      - run: echo test
  options:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        options: --mount type=bind,src=/run/../run/docker.sock,dst=/run/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _require_failure_count(document, path, 3),
        ),
        (
            "YAML aliases in mount-bearing fields",
            """
name: alias fixture
on:
  workflow_dispatch:
jobs:
  container-alias:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: &safe_options --volume /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
      volumes:
        - &safe_volume /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
    steps:
      - run: echo test
  service-alias:
    runs-on: ubuntu-24.04
    services:
      docker:
        image: docker:27
        options: *safe_options
        volumes:
          - *safe_volume
    steps:
      - run: echo test
""",
            lambda document, path: _require_failure_count(document, path, 4),
        ),
        (
            "YAML aliases wrapping container and service mappings",
            """
name: alias mapping fixture
on:
  workflow_dispatch:
jobs:
  container-anchor:
    runs-on: ubuntu-24.04
    container: &unsafe_container {image: alpine:3.20, options: '--volume /run/docker.sock:/mnt/docker.sock'}
    steps:
      - run: echo test
  container-alias:
    runs-on: ubuntu-24.04
    container: *unsafe_container
    steps:
      - run: echo test
  service-anchor:
    runs-on: ubuntu-24.04
    services:
      docker: &unsafe_service {image: docker:27, options: '--volume /run/docker.sock:/mnt/docker.sock'}
    steps:
      - run: echo test
  service-alias:
    runs-on: ubuntu-24.04
    services:
      docker: *unsafe_service
    steps:
      - run: echo test
""",
            lambda document, path: _require_failure_count(document, path, 4),
        ),
        (
            "unknown container mount option value",
            """
name: unknown option fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --volume
    steps:
      - run: echo test
""",
            lambda document, path: _default_socket_mapping_failures(document, path),
        ),
        (
            "unknown relative host paths",
            """
name: relative path fixture
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    container:
      image: alpine:3.20
      options: --volume ../run/docker.sock:/mnt/docker.sock
      volumes:
        - ./run/docker.sock:/mnt/docker.sock
    steps:
      - run: echo test
""",
            lambda document, path: _require_failure_count(document, path, 2),
        ),
        (
            "step shell override on guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    steps:
      - run: test ! -S /var/run/docker.sock
        shell: bash
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "job defaults shell override on guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    defaults:
      run:
        shell: bash
    steps:
      - run: test ! -S /var/run/docker.sock
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "workflow defaults shell override on guard",
            """
on:
  workflow_dispatch:
defaults:
  run:
    shell: bash
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    steps:
      - run: test ! -S /var/run/docker.sock
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "workflow defaults shell alias on guard",
            """
on:
  workflow_dispatch:
jobs:
  anchor:
    runs-on: ubuntu-24.04
    defaults:
      run: &unsafe_run {shell: bash}
    steps:
      - run: echo anchor
  smoke-base:
    runs-on: ubuntu-24.04
    steps:
      - run: test ! -S /var/run/docker.sock
defaults:
  run: *unsafe_run
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "job if condition on guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    if: ${{ always() }}
    steps:
      - run: test ! -S /var/run/docker.sock
""",
            lambda document, path: _base_guard_failures(document, path, "smoke-base"),
        ),
        (
            "job continue-on-error on guard",
            """
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
    continue-on-error: false
    steps:
      - run: test ! -S /var/run/docker.sock
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
        -v/run/lab-docker/docker.sock:/run/lab-docker/docker.sock
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
on:
  workflow_dispatch:
jobs:
  smoke-base:
    runs-on: ubuntu-24.04
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

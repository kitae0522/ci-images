package main

import (
	"path/filepath"
	"strings"
	"testing"
)

func requireViolation(t *testing.T, path string, source string) {
	t.Helper()
	violations := CheckWorkflow(path, []byte(source))
	if len(violations) == 0 {
		t.Fatalf("expected policy violations for %s", path)
	}
}

func requireNoViolation(t *testing.T, path string, source string) {
	t.Helper()
	violations := CheckWorkflow(path, []byte(source))
	if len(violations) != 0 {
		var messages []string
		for _, violation := range violations {
			messages = append(messages, violation.Error())
		}
		t.Fatalf("unexpected policy violations for %s:\n%s", path, strings.Join(messages, "\n"))
	}
}

func TestExactLabBaseContractPasses(t *testing.T) {
	requireNoViolation(t, ".github/workflows/lab-capacity-smoke.yml", `
name: Lab Capacity Smoke
on:
  workflow_dispatch:
jobs:
  hold:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
    steps:
      - name: Assert default Docker socket is inert
        run: test ! -S /var/run/docker.sock
      - run: echo safe
`)
}

func TestExactLabDockerContractPasses(t *testing.T) {
	requireNoViolation(t, "templates/docker-build.yml", `
jobs:
  test:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-docker:24.04
      env:
        DOCKER_HOST: unix:///run/lab-docker/docker.sock
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
      volumes:
        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
    steps:
      - uses: actions/checkout@v6
      - run: docker build .
`)
}

func TestBaseRejectsSocketAndHostPrivileges(t *testing.T) {
	requireViolation(t, ".github/workflows/lab-capacity-smoke.yml", `
name: unsafe base
on:
  workflow_dispatch:
jobs:
  hold:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
      options: >-
        --privileged
        --pid=host
        --volume /run:/run
      volumes:
        - /var/run/docker.sock:/run/docker.sock
    steps:
      - run: test ! -S /var/run/docker.sock
`)
}

func TestBaseRejectsGuardOverrides(t *testing.T) {
	requireViolation(t, ".github/workflows/lab-capacity-smoke.yml", `
name: unsafe guard
on:
  workflow_dispatch:
jobs:
  hold:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    if: ${{ always() }}
    continue-on-error: true
    defaults:
      run:
        shell: bash
    strategy:
      matrix:
        value: [one, two]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
    steps:
      - run: test ! -S /var/run/docker.sock
        if: ${{ always() }}
        shell: bash
        env:
          CHECK: enabled
        continue-on-error: true
`)
}

func TestBaseRejectsDynamicPolicyFields(t *testing.T) {
	requireViolation(t, ".github/workflows/lab-capacity-smoke.yml", `
name: dynamic base
on:
  workflow_dispatch:
    inputs:
      runner:
        required: true
        type: string
      docker_options:
        required: true
        type: string
      docker_volume:
        required: true
        type: string
jobs:
  hold:
    runs-on: ${{ inputs.runner }}
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
      options: ${{ inputs.docker_options }}
      volumes:
        - ${{ inputs.docker_volume }}
    steps:
      - run: test ! -S /var/run/docker.sock
`)
}

func TestDockerRejectsWrongEnvVolumeDestinationAndServices(t *testing.T) {
	requireViolation(t, ".github/workflows/publish.yml", `
name: unsafe docker
on:
  workflow_dispatch:
jobs:
  smoke-docker:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-docker:24.04
      env:
        DOCKER_HOST: unix:///run/lab-docker/docker.sock
        EXTRA: enabled
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
      volumes:
        - /run/lab-docker/docker.sock:/run/docker.sock
        - /run/lab-docker/extra.sock:/run/lab-docker/extra.sock
    services:
      docker:
        image: docker:27
    steps:
      - run: docker ps
`)
}

func TestUnknownAndMissingJobsFailClosed(t *testing.T) {
	requireViolation(t, ".github/workflows/lab-capacity-smoke.yml", `
name: unknown lab jobs
on:
  workflow_dispatch:
jobs:
  surprise:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
    steps:
      - run: echo surprise
`)
	requireViolation(t, ".github/workflows/publish.yml", `
name: missing jobs
on:
  workflow_dispatch:
jobs: {}
`)
}

func TestHostedJobCannotUseLabPrivileges(t *testing.T) {
	requireViolation(t, ".github/workflows/validate.yml", `
name: unsafe hosted
on:
  workflow_dispatch:
jobs:
  validate:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
    steps:
      - run: echo unsafe
`)
}

func TestUnknownWorkflowPathFailsClosed(t *testing.T) {
	requireViolation(t, ".github/workflows/unknown.yml", `
name: unknown
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - run: echo unknown
`)
}

func TestUnsafeYAMLFeaturesFailClosed(t *testing.T) {
	requireViolation(t, "templates/docker-build.yml", `
jobs:
  test:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-docker:24.04
      env:
        DOCKER_HOST: unix:///run/lab-docker/docker.sock
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
      volumes:
        - !socket /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
    steps:
      - run: docker build .
`)
	requireViolation(t, "templates/docker-build.yml", `
jobs:
  test:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-docker:24.04
      env:
        DOCKER_HOST: unix:///run/lab-docker/docker.sock
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
      volumes:
        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock
    steps:
      - run: docker build .
---
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - run: echo unexpected document
`)
}

func TestRepositoryInventoryPasses(t *testing.T) {
	root := filepath.Join("..", "..")
	violations := CheckRepository(root)
	if len(violations) != 0 {
		var messages []string
		for _, violation := range violations {
			messages = append(messages, violation.Error())
		}
		t.Fatalf("repository contract violations:\n%s", strings.Join(messages, "\n"))
	}
}

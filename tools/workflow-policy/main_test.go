package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const repositoryRoot = "../.."

func workflowSource(t *testing.T, path string) string {
	t.Helper()
	source, err := os.ReadFile(filepath.Join(repositoryRoot, filepath.FromSlash(path)))
	if err != nil {
		t.Fatalf("read workflow %s: %v", path, err)
	}
	return string(source)
}

func workflowMutation(t *testing.T, path, old, replacement string) string {
	t.Helper()
	return replaceOnce(t, workflowSource(t, path), old, replacement)
}

func replaceOnce(t *testing.T, source, old, replacement string) string {
	t.Helper()
	if !strings.Contains(source, old) {
		t.Fatalf("fixture text not found: %q", old)
	}
	return strings.Replace(source, old, replacement, 1)
}

func requireSpecificViolation(t *testing.T, path string, source string, code string, message string) {
	t.Helper()
	violations := CheckWorkflow(path, []byte(source))
	for _, actual := range violations {
		if actual.Code == code && actual.Message == message {
			return
		}
	}
	var messages []string
	for _, violation := range violations {
		messages = append(messages, violation.Code+": "+violation.Message)
	}
	t.Fatalf("expected %s %q for %s, got:\n%s", code, message, path, strings.Join(messages, "\n"))
}

func requireDiscoveryViolation(t *testing.T, violations []Violation, code, message string) {
	t.Helper()
	for _, actual := range violations {
		if actual.Code == code && actual.Message == message {
			return
		}
	}
	var messages []string
	for _, violation := range violations {
		messages = append(messages, violation.Code+": "+violation.Message)
	}
	t.Fatalf("expected discovery %s %q, got:\n%s", code, message, strings.Join(messages, "\n"))
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

func TestLabGoSetupRejectsRemoteCache(t *testing.T) {
	const path = "templates/go.yml"
	const valid = `
jobs:
  test:
    runs-on: [self-hosted, lab, linux, x64, container, lab-small]
    container:
      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04
      options: >-
        --cpus 1.5
        --memory 2560m
        --pids-limit 768
        --cgroup-parent docker-workloads-ci.slice
    steps:
      - run: test ! -S /var/run/docker.sock
      - uses: actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16
        with:
          go-version-file: go.mod
          cache: false
`
	requireNoViolation(t, path, valid)

	for _, invalid := range []string{
		replaceOnce(t, valid, "          cache: false\n", ""),
		replaceOnce(t, valid, "          cache: false\n", "          cache: true\n"),
		replaceOnce(t, valid, "          cache: false\n", "          cache: ${{ inputs.cache }}\n"),
	} {
		requireSpecificViolation(t, path, invalid, "setup_go_remote_cache_forbidden", "actions/setup-go must set with.cache to false on lab runners")
	}
}

func TestBaseRejectsSocketAndHostPrivileges(t *testing.T) {
	const path = ".github/workflows/lab-capacity-smoke.yml"
	source := workflowMutation(t, path, "        --cpus 1.5\n", "        --privileged\n")
	requireSpecificViolation(t, path, source, "container_options_mismatch", `container.options must exactly equal "--cpus 1.5 --memory 2560m --pids-limit 768 --cgroup-parent docker-workloads-ci.slice"`)

	source = workflowMutation(t, path, "        --cgroup-parent docker-workloads-ci.slice\n    timeout-minutes:", "        --cgroup-parent docker-workloads-ci.slice\n      volumes:\n        - /var/run/docker.sock:/run/docker.sock\n    timeout-minutes:")
	requireSpecificViolation(t, path, source, "container_key_forbidden", "container.volumes is not part of the closed contract")
}

func TestBaseRejectsGuardOverrides(t *testing.T) {
	const path = ".github/workflows/lab-capacity-smoke.yml"
	fixtures := []struct {
		old, replacement, code, message string
	}{
		{
			old:         "        run: test ! -S /var/run/docker.sock\n",
			replacement: "        run: test ! -S /var/run/docker.sock\n        if: ${{ always() }}\n",
			code:        "guard_step_if_forbidden",
			message:     "steps[0].if must not make the guard skippable",
		},
		{
			old:         "        run: test ! -S /var/run/docker.sock\n",
			replacement: "        run: test ! -S /var/run/docker.sock\n        shell: bash\n",
			code:        "guard_step_shell_forbidden",
			message:     "steps[0].shell must not override the guard shell",
		},
		{
			old:         "        run: test ! -S /var/run/docker.sock\n",
			replacement: "        run: test ! -S /var/run/docker.sock\n        env:\n          CHECK: enabled\n",
			code:        "guard_step_env_forbidden",
			message:     "steps[0].env must not override the guard",
		},
		{
			old:         "        run: test ! -S /var/run/docker.sock\n",
			replacement: "        run: test ! -S /var/run/docker.sock\n        continue-on-error: false\n",
			code:        "guard_step_continue_forbidden",
			message:     "steps[0].continue-on-error must not override the guard",
		},
		{
			old:         "        run: test ! -S /var/run/docker.sock\n",
			replacement: "        run: test ! -S /var/run/docker.sock\n        timeout-minutes: 1\n",
			code:        "guard_step_timeout_forbidden",
			message:     "steps[0].timeout-minutes must not override the guard",
		},
		{
			old:         "  hold:\n",
			replacement: "  hold:\n    if: ${{ always() }}\n",
			code:        "guard_job_if_forbidden",
			message:     "job.if must not make the guard skippable",
		},
		{
			old:         "  hold:\n",
			replacement: "  hold:\n    continue-on-error: false\n",
			code:        "guard_job_continue_forbidden",
			message:     "job.continue-on-error must not override the guard",
		},
		{
			old:         "  hold:\n",
			replacement: "  hold:\n    defaults:\n      run:\n        shell: bash\n",
			code:        "guard_job_defaults_forbidden",
			message:     "job.defaults must not override the guard shell",
		},
		{
			old:         "  hold:\n",
			replacement: "  hold:\n    strategy:\n      matrix:\n        value: [one, two]\n",
			code:        "guard_job_strategy_forbidden",
			message:     "job.strategy must not modify guarded execution",
		},
	}
	for _, fixture := range fixtures {
		source := workflowMutation(t, path, fixture.old, fixture.replacement)
		requireSpecificViolation(t, path, source, fixture.code, fixture.message)
	}
	workflow := workflowSource(t, path)
	workflow = replaceOnce(t, workflow, "permissions:\n  contents: read\n", "defaults:\n  run:\n    shell: bash\n\npermissions:\n  contents: read\n")
	requireSpecificViolation(t, path, workflow, "guard_workflow_defaults_forbidden", "workflow.defaults must not override the guard shell")
}

func TestBaseRejectsDynamicPolicyFields(t *testing.T) {
	const path = ".github/workflows/lab-capacity-smoke.yml"
	fixtures := []struct {
		old, replacement, code, message string
	}{
		{
			old:         "    runs-on: [self-hosted, lab, linux, x64, container, lab-small]\n",
			replacement: "    runs-on: ${{ inputs.runner }}\n",
			code:        "runner_dynamic",
			message:     "runs-on expressions and runner groups are not allowed",
		},
		{
			old:         "      options: >-\n        --cpus 1.5\n        --memory 2560m\n        --pids-limit 768\n        --cgroup-parent docker-workloads-ci.slice\n",
			replacement: "      options: ${{ inputs.docker_options }}\n",
			code:        "container_options_mismatch",
			message:     `container.options must exactly equal "--cpus 1.5 --memory 2560m --pids-limit 768 --cgroup-parent docker-workloads-ci.slice"`,
		},
		{
			old:         "      image: ghcr.io/kitae0522/ci-ubuntu-base:24.04\n",
			replacement: "      image: ${{ inputs.image }}\n",
			code:        "container_image_mismatch",
			message:     `container.image must exactly equal "ghcr.io/kitae0522/ci-ubuntu-base:24.04"`,
		},
	}
	for _, fixture := range fixtures {
		source := workflowMutation(t, path, fixture.old, fixture.replacement)
		requireSpecificViolation(t, path, source, fixture.code, fixture.message)
	}
}

func TestDockerRejectsWrongEnvVolumeDestinationAndServices(t *testing.T) {
	const path = ".github/workflows/publish.yml"
	fixtures := []struct {
		old, replacement, code, message string
	}{
		{
			old:         "        DOCKER_HOST: unix:///run/lab-docker/docker.sock\n",
			replacement: "        DOCKER_HOST: unix:///run/lab-docker/docker.sock\n        EXTRA: enabled\n",
			code:        "docker_env_keys",
			message:     "container.env must contain only DOCKER_HOST",
		},
		{
			old:         "        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock\n",
			replacement: "        - /run/lab-docker/docker.sock:/run/docker.sock\n",
			code:        "docker_volume_mismatch",
			message:     `container.volumes must contain only "/run/lab-docker/docker.sock:/run/lab-docker/docker.sock"`,
		},
		{
			old:         "      volumes:\n        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock\n    steps:\n      - uses: actions/checkout@",
			replacement: "      volumes:\n        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock\n    services:\n      docker:\n        image: docker:27\n    steps:\n      - uses: actions/checkout@",
			code:        "services_forbidden",
			message:     "services are forbidden for lab jobs",
		},
	}
	for _, fixture := range fixtures {
		source := workflowMutation(t, path, fixture.old, fixture.replacement)
		requireSpecificViolation(t, path, source, fixture.code, fixture.message)
	}
}

func TestUnknownAndMissingJobsFailClosed(t *testing.T) {
	const unknownPath = ".github/workflows/lab-capacity-smoke.yml"
	unknown := workflowSource(t, unknownPath) + `
  surprise:
    runs-on: ubuntu-24.04
    steps:
      - run: echo surprise
`
	requireSpecificViolation(t, unknownPath, unknown, "job_not_allowlisted", "job is not in the closed allowlist")

	const missingPath = ".github/workflows/publish.yml"
	missing := workflowSource(t, missingPath)
	promoteStart := strings.Index(missing, "  promote:\n")
	if promoteStart < 0 {
		t.Fatal("publish promote job not found")
	}
	missing = missing[:promoteStart]
	requireSpecificViolation(t, missingPath, missing, "job_missing", "allowlisted job is missing")
}

func TestHostedJobCannotUseLabPrivileges(t *testing.T) {
	const path = ".github/workflows/validate.yml"
	source := workflowMutation(t, path, "    runs-on: ubuntu-24.04\n", "    runs-on: [self-hosted, lab, linux, x64, container, lab-small]\n")
	requireSpecificViolation(t, path, source, "runner_mismatch", "runs-on labels must exactly equal [ubuntu-24.04]")

	source = workflowSource(t, path)
	source = replaceOnce(t, source, "    timeout-minutes: 30\n", "    timeout-minutes: 30\n    container: null\n")
	requireSpecificViolation(t, path, source, "hosted_container_forbidden", "container is forbidden for hosted jobs")
}

func TestUnknownWorkflowPathFailsClosed(t *testing.T) {
	source := workflowSource(t, ".github/workflows/validate.yml")
	requireSpecificViolation(t, "templates/hidden/.github/workflows/validate.yml", source, "workflow_not_allowlisted", "workflow or template is not allowlisted")
	requireSpecificViolation(t, "templates/../.github/workflows/validate.yml", source, "workflow_not_allowlisted", "workflow or template is not allowlisted")
	requireSpecificViolation(t, "../.github/workflows/validate.yml", source, "workflow_not_allowlisted", "workflow or template is not allowlisted")
	requireSpecificViolation(t, ".github/workflows/unknown.yml", source, "workflow_not_allowlisted", "workflow or template is not allowlisted")
	requireSpecificViolation(t, ".github\\workflows\\validate.yml", source, "workflow_not_allowlisted", "workflow or template is not allowlisted")
}

func TestUnsafeYAMLFeaturesFailClosed(t *testing.T) {
	const path = "templates/docker-build.yml"
	source := workflowMutation(t, path, "        - /run/lab-docker/docker.sock:/run/lab-docker/docker.sock\n", "        - !socket /run/lab-docker/docker.sock:/run/lab-docker/docker.sock\n")
	requireSpecificViolation(t, path, source, "yaml_unsafe", "YAML anchors, aliases, and custom tags are not allowed in policy files")
	requireSpecificViolation(t, path, workflowSource(t, path)+"\n---\njobs: {}\n", "yaml_parse_failed", "YAML document parse failed: YAML document must contain exactly one document")
}

func TestRepositoryRejectsPolicySymlink(t *testing.T) {
	root := t.TempDir()
	workflowDir := filepath.Join(root, ".github", "workflows")
	if err := os.MkdirAll(workflowDir, 0o755); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "outside.yml")
	if err := os.WriteFile(target, []byte("name: outside\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(workflowDir, "validate.yml")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	_, violations := discoverPolicyFiles(root)
	requireDiscoveryViolation(t, violations, "policy_symlink", "symbolic links are not allowed in policy paths")
}

func TestRepositoryRejectsAncestorPolicySymlinks(t *testing.T) {
	tests := []struct {
		name     string
		linkPath []string
	}{
		{
			name:     "github ancestor",
			linkPath: []string{".github"},
		},
		{
			name:     "templates ancestor",
			linkPath: []string{"templates"},
		},
		{
			name:     "workflows intermediate",
			linkPath: []string{".github", "workflows"},
		},
		{
			name:     "templates intermediate",
			linkPath: []string{"templates", "nested"},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			target := filepath.Join(filepath.Dir(root), filepath.Base(root)+"-external")
			t.Cleanup(func() { _ = os.RemoveAll(target) })
			if err := os.MkdirAll(target, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(target, "validate.yml"), []byte("name: outside\n"), 0o644); err != nil {
				t.Fatal(err)
			}

			link := filepath.Join(append([]string{root}, test.linkPath...)...)
			if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(filepath.Join("..", filepath.Base(target)), link); err != nil {
				t.Fatal(err)
			}

			_, violations := discoverPolicyFiles(root)
			requireDiscoveryViolation(t, violations, "policy_symlink", "symbolic links are not allowed in policy paths")
		})
	}
}

func TestRepositoryRejectsNonDirectoryPolicyComponents(t *testing.T) {
	tests := []struct {
		name       string
		filePath   []string
		parentPath []string
	}{
		{
			name:       "github ancestor file",
			filePath:   []string{".github"},
			parentPath: nil,
		},
		{
			name:       "workflows root file",
			filePath:   []string{".github", "workflows"},
			parentPath: []string{".github"},
		},
		{
			name:       "templates root file",
			filePath:   []string{"templates"},
			parentPath: nil,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			if len(test.parentPath) > 0 {
				parent := filepath.Join(append([]string{root}, test.parentPath...)...)
				if err := os.MkdirAll(parent, 0o755); err != nil {
					t.Fatal(err)
				}
			}
			file := filepath.Join(append([]string{root}, test.filePath...)...)
			if err := os.WriteFile(file, []byte("not a directory\n"), 0o644); err != nil {
				t.Fatal(err)
			}

			_, violations := discoverPolicyFiles(root)
			requireDiscoveryViolation(t, violations, "policy_path_not_directory", "policy path component is not a directory")
		})
	}
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

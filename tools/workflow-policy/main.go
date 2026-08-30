package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	pathpkg "path"
	"path/filepath"
	"sort"
	"strings"

	"github.com/rhysd/actionlint"
	"go.yaml.in/yaml/v4"
)

const (
	inertSocketCheck = "test ! -S /var/run/docker.sock"
	dockerHost       = "unix:///run/lab-docker/docker.sock"
	alternateVolume  = "/run/lab-docker/docker.sock:/run/lab-docker/docker.sock"
	resourceOptions  = "--cpus 1.5 --memory 2560m --pids-limit 768 --cgroup-parent docker-workloads-ci.slice"
)

var labLabels = []string{"self-hosted", "lab", "linux", "x64", "container", "lab-small"}

type contractKind uint8

const (
	hostedContract contractKind = iota
	labBaseContract
	labDockerContract
)

type jobContract struct {
	kind  contractKind
	image string
}

var workflowContracts = map[string]map[string]jobContract{
	".github/workflows/validate.yml": {
		"validate": {kind: hostedContract},
	},
	".github/workflows/publish.yml": {
		"candidate":    {kind: hostedContract},
		"smoke-base":   {kind: labBaseContract, image: "ghcr.io/kitae0522/ci-ubuntu-base@${{ needs.candidate.outputs.base_digest }}"},
		"smoke-docker": {kind: labDockerContract, image: "ghcr.io/kitae0522/ci-ubuntu-docker@${{ needs.candidate.outputs.docker_digest }}"},
		"promote":      {kind: hostedContract},
	},
	".github/workflows/lab-capacity-smoke.yml": {
		"hold": {kind: labBaseContract, image: "ghcr.io/kitae0522/ci-ubuntu-base:24.04"},
	},
	"templates/go.yml": {
		"test": {kind: labBaseContract, image: "ghcr.io/kitae0522/ci-ubuntu-base:24.04"},
	},
	"templates/docker-build.yml": {
		"test": {kind: labDockerContract, image: "ghcr.io/kitae0522/ci-ubuntu-docker:24.04"},
	},
}

type Violation struct {
	Code    string
	Path    string
	Job     string
	Message string
}

func (v Violation) Error() string {
	if v.Job == "" {
		return fmt.Sprintf("%s: %s", v.Path, v.Message)
	}
	return fmt.Sprintf("%s#%s: %s", v.Path, v.Job, v.Message)
}

func violationWithCode(code, path, job, format string, args ...any) Violation {
	return Violation{Code: code, Path: path, Job: job, Message: fmt.Sprintf(format, args...)}
}

func normalizePolicyPath(path string) string {
	// Policy paths are repository-root-relative. Do not search for an embedded
	// marker: a nested path or a traversal must never impersonate an allowlisted
	// workflow. Backslashes are treated as separators for cross-platform calls.
	slash := strings.ReplaceAll(path, "\\", "/")
	if slash == "" || pathpkg.IsAbs(slash) || strings.HasPrefix(slash, "/") ||
		(len(slash) >= 2 && slash[1] == ':') {
		return ""
	}
	for _, component := range strings.Split(slash, "/") {
		if component == ".." {
			return ""
		}
	}
	cleaned := pathpkg.Clean(slash)
	if cleaned == "." || cleaned == ".." || strings.HasPrefix(cleaned, "../") {
		return ""
	}
	return cleaned
}

func isTemplatePath(path string) bool {
	return strings.HasPrefix(normalizePolicyPath(path), "templates/")
}

func parseActionlint(path string, source []byte) (*actionlint.Workflow, []*actionlint.Error) {
	if !isTemplatePath(path) {
		return actionlint.Parse(source)
	}

	// Templates are job fragments rather than dispatchable workflow files. Add
	// a synthetic event only for actionlint's parser; raw YAML validation still
	// uses the original source below.
	wrapped := make([]byte, 0, len(source)+32)
	wrapped = append(wrapped, []byte("on:\n  workflow_dispatch:\n")...)
	wrapped = append(wrapped, source...)
	return actionlint.Parse(wrapped)
}

func parseRawDocument(source []byte) (*yaml.Node, error) {
	// actionlint v1.7.12 exposes Container.Volumes but currently parses that
	// field into Container.Ports. Keep actionlint as the authoritative workflow
	// AST and inspect this one field with the same YAML AST for the closed
	// volume contract.
	decoder := yaml.NewDecoder(bytes.NewReader(source))
	var document yaml.Node
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	if len(document.Content) != 1 {
		return nil, errors.New("YAML document must contain exactly one root node")
	}
	var extra yaml.Node
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, errors.New("YAML document must contain exactly one document")
		}
		return nil, err
	}
	return document.Content[0], nil
}

func mappingEntries(node *yaml.Node) (map[string]*yaml.Node, bool) {
	if node == nil || node.Kind != yaml.MappingNode {
		return nil, false
	}
	entries := make(map[string]*yaml.Node, len(node.Content)/2)
	for index := 0; index+1 < len(node.Content); index += 2 {
		key := node.Content[index]
		if key.Kind != yaml.ScalarNode {
			return nil, false
		}
		entries[key.Value] = node.Content[index+1]
	}
	return entries, true
}

func mappingField(node *yaml.Node, key string) (*yaml.Node, bool) {
	entries, ok := mappingEntries(node)
	if !ok {
		return nil, false
	}
	value, found := entries[key]
	return value, found
}

var safeYAMLTags = map[string]bool{
	"":            true,
	"!!binary":    true,
	"!!bool":      true,
	"!!float":     true,
	"!!int":       true,
	"!!map":       true,
	"!!null":      true,
	"!!seq":       true,
	"!!str":       true,
	"!!timestamp": true,
}

func containsUnsafeYAMLFeature(node *yaml.Node, visited map[*yaml.Node]bool) bool {
	if node == nil || visited[node] {
		return false
	}
	visited[node] = true
	if node.Kind == yaml.AliasNode || node.Anchor != "" || !safeYAMLTags[node.Tag] {
		return true
	}
	for _, child := range node.Content {
		if containsUnsafeYAMLFeature(child, visited) {
			return true
		}
	}
	return false
}

func containsDynamic(value string) bool {
	return strings.Contains(value, "${{")
}

func stringValue(value *actionlint.String) (string, bool) {
	if value == nil {
		return "", false
	}
	return value.Value, true
}

func checkRunner(path, jobName string, job *actionlint.Job, expected []string) []Violation {
	if job.RunsOn == nil {
		return []Violation{violationWithCode("runner_missing", path, jobName, "runs-on must be explicitly allowlisted")}
	}
	if job.RunsOn.LabelsExpr != nil || job.RunsOn.Group != nil {
		return []Violation{violationWithCode("runner_dynamic", path, jobName, "runs-on expressions and runner groups are not allowed")}
	}
	if len(job.RunsOn.Labels) != len(expected) {
		return []Violation{violationWithCode("runner_mismatch", path, jobName, "runs-on labels must exactly equal %v", expected)}
	}
	for index, label := range job.RunsOn.Labels {
		if label == nil || label.Value != expected[index] || containsDynamic(label.Value) {
			return []Violation{violationWithCode("runner_mismatch", path, jobName, "runs-on labels must exactly equal %v", expected)}
		}
	}
	return nil
}

func checkContainerKeys(path, jobName string, rawContainer *yaml.Node, allowed map[string]bool) []Violation {
	entries, ok := mappingEntries(rawContainer)
	if !ok {
		return []Violation{violationWithCode("container_mapping_invalid", path, jobName, "container must be a mapping")}
	}
	var violations []Violation
	for key := range entries {
		if !allowed[key] {
			violations = append(violations, violationWithCode("container_key_forbidden", path, jobName, "container.%s is not part of the closed contract", key))
		}
	}
	return violations
}

func checkImage(path, jobName string, container *actionlint.Container, expected string) []Violation {
	actual, ok := stringValue(container.Image)
	if !ok || actual != expected {
		return []Violation{violationWithCode("container_image_mismatch", path, jobName, "container.image must exactly equal %q", expected)}
	}
	return nil
}

func checkResourceOptions(path, jobName string, container *actionlint.Container) []Violation {
	actual, ok := stringValue(container.Options)
	if !ok || actual != resourceOptions || containsDynamic(actual) {
		return []Violation{violationWithCode("container_options_mismatch", path, jobName, "container.options must exactly equal %q", resourceOptions)}
	}
	return nil
}

func checkNoServices(path, jobName string, job *actionlint.Job, rawJob *yaml.Node) []Violation {
	if job.Services != nil {
		return []Violation{violationWithCode("services_forbidden", path, jobName, "services are forbidden for lab jobs")}
	}
	if _, found := mappingField(rawJob, "services"); found {
		return []Violation{violationWithCode("services_forbidden", path, jobName, "services are forbidden for lab jobs")}
	}
	return nil
}

func checkRawContainerField(path, jobName string, rawContainer *yaml.Node, field string, expectedKind yaml.Kind) (*yaml.Node, []Violation) {
	node, found := mappingField(rawContainer, field)
	if !found {
		return nil, []Violation{violationWithCode("container_field_missing", path, jobName, "container.%s is required by the closed contract", field)}
	}
	if node == nil || node.Kind != expectedKind {
		return nil, []Violation{violationWithCode("container_field_invalid", path, jobName, "container.%s has an unknown value", field)}
	}
	return node, nil
}

func checkGuard(path, jobName string, workflow *actionlint.Workflow, job *actionlint.Job, rawRoot, rawJob *yaml.Node) []Violation {
	var violations []Violation
	if len(job.Steps) == 0 || job.Steps[0] == nil {
		return []Violation{violationWithCode("guard_missing", path, jobName, "steps must start with the inert Docker socket guard")}
	}
	first := job.Steps[0]
	run, isRun := first.Exec.(*actionlint.ExecRun)
	if !isRun || run == nil || run.Run == nil || run.Run.Value != inertSocketCheck {
		violations = append(violations, violationWithCode("guard_step_mismatch", path, jobName, "steps[0].run must exactly equal %q", inertSocketCheck))
	} else {
		if run.Shell != nil {
			violations = append(violations, violationWithCode("guard_step_shell_forbidden", path, jobName, "steps[0].shell must not override the guard shell"))
		}
		if run.WorkingDirectory != nil {
			violations = append(violations, violationWithCode("guard_step_working_directory_forbidden", path, jobName, "steps[0].working-directory must not override the guard"))
		}
	}
	rawFirstStep, rawFirstStepFound := rawFirstStep(rawJob)
	if first.If != nil || (rawFirstStepFound && hasMappingField(rawFirstStep, "if")) {
		violations = append(violations, violationWithCode("guard_step_if_forbidden", path, jobName, "steps[0].if must not make the guard skippable"))
	}
	if first.Env != nil || (rawFirstStepFound && hasMappingField(rawFirstStep, "env")) {
		violations = append(violations, violationWithCode("guard_step_env_forbidden", path, jobName, "steps[0].env must not override the guard"))
	}
	if first.ContinueOnError != nil || (rawFirstStepFound && hasMappingField(rawFirstStep, "continue-on-error")) {
		violations = append(violations, violationWithCode("guard_step_continue_forbidden", path, jobName, "steps[0].continue-on-error must not override the guard"))
	}
	if first.TimeoutMinutes != nil || (rawFirstStepFound && hasMappingField(rawFirstStep, "timeout-minutes")) {
		violations = append(violations, violationWithCode("guard_step_timeout_forbidden", path, jobName, "steps[0].timeout-minutes must not override the guard"))
	}
	if job.If != nil || hasMappingField(rawJob, "if") {
		violations = append(violations, violationWithCode("guard_job_if_forbidden", path, jobName, "job.if must not make the guard skippable"))
	}
	if job.ContinueOnError != nil || hasMappingField(rawJob, "continue-on-error") {
		violations = append(violations, violationWithCode("guard_job_continue_forbidden", path, jobName, "job.continue-on-error must not override the guard"))
	}
	if job.Defaults != nil || hasMappingField(rawJob, "defaults") {
		violations = append(violations, violationWithCode("guard_job_defaults_forbidden", path, jobName, "job.defaults must not override the guard shell"))
	}
	if job.Strategy != nil || hasMappingField(rawJob, "strategy") {
		violations = append(violations, violationWithCode("guard_job_strategy_forbidden", path, jobName, "job.strategy must not modify guarded execution"))
	}
	if workflow.Defaults != nil || hasMappingField(rawRoot, "defaults") {
		violations = append(violations, violationWithCode("guard_workflow_defaults_forbidden", path, jobName, "workflow.defaults must not override the guard shell"))
	}
	return violations
}

func hasMappingField(node *yaml.Node, key string) bool {
	_, found := mappingField(node, key)
	return found
}

func rawFirstStep(rawJob *yaml.Node) (*yaml.Node, bool) {
	rawSteps, found := mappingField(rawJob, "steps")
	if !found || rawSteps == nil || rawSteps.Kind != yaml.SequenceNode || len(rawSteps.Content) == 0 {
		return nil, false
	}
	first := rawSteps.Content[0]
	if first == nil || first.Kind != yaml.MappingNode {
		return nil, false
	}
	return first, true
}

func checkLabBase(path, jobName string, contract jobContract, workflow *actionlint.Workflow, job *actionlint.Job, rawRoot, rawJob *yaml.Node) []Violation {
	violations := checkRunner(path, jobName, job, labLabels)
	violations = append(violations, checkNoServices(path, jobName, job, rawJob)...)
	if job.Container == nil {
		return append(violations, violationWithCode("container_required", path, jobName, "container is required for lab-base jobs"))
	}
	rawContainer, found := mappingField(rawJob, "container")
	if !found || rawContainer == nil || rawContainer.Kind != yaml.MappingNode {
		violations = append(violations, violationWithCode("container_mapping_invalid", path, jobName, "container must be an explicit mapping"))
	} else {
		violations = append(violations, checkContainerKeys(path, jobName, rawContainer, map[string]bool{
			"image":       true,
			"credentials": true,
			"options":     true,
		})...)
	}
	violations = append(violations, checkImage(path, jobName, job.Container, contract.image)...)
	violations = append(violations, checkResourceOptions(path, jobName, job.Container)...)
	// The raw document is used only to detect fields whose value is null or
	// otherwise omitted by actionlint's typed AST; the guard text itself still
	// comes from actionlint's parsed step.
	violations = append(violations, checkGuard(path, jobName, workflow, job, rawRoot, rawJob)...)
	return violations
}

func checkDockerEnvironment(path, jobName string, container *actionlint.Container, rawContainer *yaml.Node) []Violation {
	var violations []Violation
	if container.Env == nil || container.Env.Expression != nil {
		violations = append(violations, violationWithCode("docker_env_invalid", path, jobName, "container.env must contain only DOCKER_HOST"))
	} else if len(container.Env.Vars) != 1 {
		violations = append(violations, violationWithCode("docker_env_keys", path, jobName, "container.env must contain only DOCKER_HOST"))
	} else if envVar, ok := container.Env.Vars["docker_host"]; !ok || envVar == nil || envVar.Value == nil || envVar.Value.Value != dockerHost || containsDynamic(envVar.Value.Value) {
		violations = append(violations, violationWithCode("docker_env_value", path, jobName, "container.env.DOCKER_HOST must exactly equal %q", dockerHost))
	}
	envNode, found := mappingField(rawContainer, "env")
	if !found || envNode == nil || envNode.Kind != yaml.MappingNode {
		violations = append(violations, violationWithCode("docker_env_invalid", path, jobName, "container.env must be an explicit mapping"))
	} else if entries, ok := mappingEntries(envNode); !ok || len(entries) != 1 {
		violations = append(violations, violationWithCode("docker_env_keys", path, jobName, "container.env must contain exactly one variable"))
	} else if value, ok := mappingField(envNode, "DOCKER_HOST"); !ok || value == nil || value.Kind != yaml.ScalarNode || value.Value != dockerHost || containsDynamic(value.Value) {
		violations = append(violations, violationWithCode("docker_env_value", path, jobName, "container.env.DOCKER_HOST must exactly equal %q", dockerHost))
	}
	return violations
}

func checkDockerVolumes(path, jobName string, rawContainer *yaml.Node) []Violation {
	volumesNode, violations := checkRawContainerField(path, jobName, rawContainer, "volumes", yaml.SequenceNode)
	if len(violations) > 0 {
		return violations
	}
	if len(volumesNode.Content) != 1 {
		return []Violation{violationWithCode("docker_volume_count", path, jobName, "container.volumes must contain exactly the alternate socket mapping")}
	}
	volume := volumesNode.Content[0]
	if volume == nil || volume.Kind != yaml.ScalarNode || volume.Value != alternateVolume || containsDynamic(volume.Value) {
		return []Violation{violationWithCode("docker_volume_mismatch", path, jobName, "container.volumes must contain only %q", alternateVolume)}
	}
	return nil
}

func checkLabDocker(path, jobName string, contract jobContract, job *actionlint.Job, rawJob *yaml.Node) []Violation {
	violations := checkRunner(path, jobName, job, labLabels)
	violations = append(violations, checkNoServices(path, jobName, job, rawJob)...)
	if job.Container == nil {
		return append(violations, violationWithCode("container_required", path, jobName, "container is required for lab-docker jobs"))
	}
	rawContainer, found := mappingField(rawJob, "container")
	if !found || rawContainer == nil || rawContainer.Kind != yaml.MappingNode {
		violations = append(violations, violationWithCode("container_mapping_invalid", path, jobName, "container must be an explicit mapping"))
	} else {
		violations = append(violations, checkContainerKeys(path, jobName, rawContainer, map[string]bool{
			"image":       true,
			"credentials": true,
			"env":         true,
			"options":     true,
			"volumes":     true,
		})...)
		violations = append(violations, checkDockerEnvironment(path, jobName, job.Container, rawContainer)...)
		violations = append(violations, checkDockerVolumes(path, jobName, rawContainer)...)
	}
	violations = append(violations, checkImage(path, jobName, job.Container, contract.image)...)
	violations = append(violations, checkResourceOptions(path, jobName, job.Container)...)
	return violations
}

func checkHosted(path, jobName string, job *actionlint.Job, rawJob *yaml.Node) []Violation {
	violations := checkRunner(path, jobName, job, []string{"ubuntu-24.04"})
	if job.Container != nil || hasMappingField(rawJob, "container") {
		violations = append(violations, violationWithCode("hosted_container_forbidden", path, jobName, "container is forbidden for hosted jobs"))
	}
	if job.Services != nil || hasMappingField(rawJob, "services") {
		violations = append(violations, violationWithCode("hosted_services_forbidden", path, jobName, "services are forbidden for hosted jobs"))
	}
	return violations
}

func checkJob(path, jobName string, contract jobContract, workflow *actionlint.Workflow, job *actionlint.Job, rawRoot, rawJob *yaml.Node) []Violation {
	if job == nil {
		return []Violation{violationWithCode("job_missing", path, jobName, "allowlisted job is missing")}
	}
	if rawJob == nil || rawJob.Kind != yaml.MappingNode {
		return []Violation{violationWithCode("job_mapping_invalid", path, jobName, "job must be a mapping")}
	}
	switch contract.kind {
	case hostedContract:
		return checkHosted(path, jobName, job, rawJob)
	case labBaseContract:
		return checkLabBase(path, jobName, contract, workflow, job, rawRoot, rawJob)
	case labDockerContract:
		return checkLabDocker(path, jobName, contract, job, rawJob)
	default:
		return []Violation{violationWithCode("contract_unknown", path, jobName, "unknown contract kind")}
	}
}

func parseErrors(path string, errors []*actionlint.Error) []Violation {
	violations := make([]Violation, 0, len(errors))
	for _, parseError := range errors {
		if parseError == nil {
			continue
		}
		violations = append(violations, violationWithCode("actionlint_parse_error", path, "", "actionlint %s at line %d column %d: %s", parseError.Kind, parseError.Line, parseError.Column, parseError.Message))
	}
	return violations
}

func CheckWorkflow(path string, source []byte) []Violation {
	originalPath := strings.ReplaceAll(path, "\\", "/")
	path = normalizePolicyPath(path)
	if path == "" {
		return []Violation{violationWithCode("workflow_not_allowlisted", originalPath, "", "workflow or template is not allowlisted")}
	}
	contract, knownPath := workflowContracts[path]
	if !knownPath {
		return []Violation{violationWithCode("workflow_not_allowlisted", path, "", "workflow or template is not allowlisted")}
	}

	workflow, actionlintErrors := parseActionlint(path, source)
	violations := parseErrors(path, actionlintErrors)
	rawRoot, rawError := parseRawDocument(source)
	if rawError != nil {
		violations = append(violations, violationWithCode("yaml_parse_failed", path, "", "YAML document parse failed: %v", rawError))
		return violations
	}
	if containsUnsafeYAMLFeature(rawRoot, make(map[*yaml.Node]bool)) {
		violations = append(violations, violationWithCode("yaml_unsafe", path, "", "YAML anchors, aliases, and custom tags are not allowed in policy files"))
	}
	if workflow == nil {
		return violations
	}

	rawJobs, jobsMapping := mappingField(rawRoot, "jobs")
	rawJobEntries, rawJobsAreMapping := mappingEntries(rawJobs)
	if !jobsMapping || !rawJobsAreMapping {
		return append(violations, violationWithCode("jobs_mapping_invalid", path, "", "jobs must be an explicit mapping"))
	}
	for jobName := range rawJobEntries {
		if _, expected := contract[jobName]; !expected {
			violations = append(violations, violationWithCode("job_not_allowlisted", path, jobName, "job is not in the closed allowlist"))
		}
	}
	for jobName, jobContract := range contract {
		job := workflow.Jobs[jobName]
		rawJob := rawJobEntries[jobName]
		violations = append(violations, checkJob(path, jobName, jobContract, workflow, job, rawRoot, rawJob)...)
	}
	return violations
}

func discoverPolicyFiles(root string) (map[string][]byte, []Violation) {
	files := make(map[string][]byte)
	var violations []Violation
	if info, err := os.Lstat(root); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return files, []Violation{violationWithCode("policy_symlink", filepath.ToSlash(root), "", "symbolic links are not allowed in policy paths")}
	}
	for _, directory := range []string{".github/workflows", "templates"} {
		base := filepath.Join(root, directory)
		if info, err := os.Lstat(base); err == nil && info.Mode()&os.ModeSymlink != 0 {
			violations = append(violations, violationWithCode("policy_symlink", filepath.ToSlash(directory), "", "symbolic links are not allowed in policy paths"))
			continue
		}
		walkError := filepath.WalkDir(base, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.Type()&os.ModeSymlink != 0 {
				relative, relErr := filepath.Rel(root, path)
				if relErr != nil {
					return relErr
				}
				violations = append(violations, violationWithCode("policy_symlink", filepath.ToSlash(relative), "", "symbolic links are not allowed in policy paths"))
				return nil
			}
			if entry.IsDir() {
				return nil
			}
			extension := strings.ToLower(filepath.Ext(entry.Name()))
			if extension != ".yml" && extension != ".yaml" {
				return nil
			}
			relative, err := filepath.Rel(root, path)
			if err != nil {
				return err
			}
			policyPath := filepath.ToSlash(relative)
			if normalizePolicyPath(policyPath) != policyPath {
				return fmt.Errorf("non-canonical policy path %q", policyPath)
			}
			contents, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			files[policyPath] = contents
			return nil
		})
		if walkError != nil {
			if os.IsNotExist(walkError) {
				continue
			}
			violations = append(violations, violationWithCode("policy_enumeration_failed", directory, "", "unable to enumerate policy files: %v", walkError))
		}
	}
	return files, violations
}

func CheckRepository(root string) []Violation {
	files, violations := discoverPolicyFiles(root)
	paths := make([]string, 0, len(files))
	for path := range files {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, path := range paths {
		violations = append(violations, CheckWorkflow(path, files[path])...)
	}
	expectedPaths := make([]string, 0, len(workflowContracts))
	for path := range workflowContracts {
		expectedPaths = append(expectedPaths, path)
	}
	sort.Strings(expectedPaths)
	for _, path := range expectedPaths {
		if _, found := files[path]; !found {
			violations = append(violations, violationWithCode("workflow_missing", path, "", "allowlisted workflow or template is missing"))
		}
	}
	return violations
}

func main() {
	root := "."
	if len(os.Args) > 1 {
		root = os.Args[1]
	}
	if len(os.Args) > 2 {
		fmt.Fprintln(os.Stderr, "usage: workflow-policy [repository-root]")
		os.Exit(2)
	}
	violations := CheckRepository(root)
	if len(violations) > 0 {
		for _, policyViolation := range violations {
			fmt.Fprintln(os.Stderr, policyViolation.Error())
		}
		os.Exit(1)
	}
	fmt.Println("validated closed workflow socket policy")
}

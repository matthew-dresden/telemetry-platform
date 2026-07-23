//go:build terratest

package withalb_test

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// mustTaggingVars reads PROJECT_TAG and TERRATEST_RUN_ID from the environment.
// It calls t.Fatal with an actionable error message when either variable is unset.
// ExtraVars override committed tfvars values (spec section 4.3, D-12).
func mustTaggingVars(t *testing.T) map[string]interface{} {
	t.Helper()
	runID := os.Getenv("TERRATEST_RUN_ID")
	if runID == "" {
		t.Fatal("ERROR: TERRATEST_RUN_ID is not set; run via make tf-test")
	}
	projectTag := os.Getenv("PROJECT_TAG")
	if projectTag == "" {
		t.Fatal("ERROR: PROJECT_TAG is not set; run via make tf-test")
	}
	return map[string]interface{}{
		"project_tag":      projectTag,
		"terratest_run_id": runID,
	}
}

// mergeExtraVars merges tagging variables into an existing ExtraVars map.
// It calls t.Fatal when a key collision is detected to prevent silent overwrites.
func mergeExtraVars(t *testing.T, base map[string]interface{}, tagging map[string]interface{}) map[string]interface{} {
	t.Helper()
	result := make(map[string]interface{}, len(base)+len(tagging))
	for k, v := range base {
		result[k] = v
	}
	for k, v := range tagging {
		if _, exists := result[k]; exists {
			t.Fatalf("ExtraVars collision: key %q is defined both in the test and in tagging vars", k)
		}
		result[k] = v
	}
	return result
}

// TestEcsAppDeployWithAlb applies the with-alb example once, runs all output and idempotency
// assertions as subtests sharing the applied state, and destroys via t.Cleanup when the
// parent test ends. This ensures destroy runs after all subtests complete (not after the
// first subtest), satisfying FR-3 tag wiring and the D-12 no-premature-destroy rule.
// The example fixture is self-contained: it generates a self-signed TLS certificate using the
// tls provider and imports it into ACM, so no external certificate or DNS registration is required.
func TestEcsAppDeployWithAlb(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("ecs-w-%s", suffix)

	tagging := mustTaggingVars(t)
	extraVars := mergeExtraVars(t, map[string]interface{}{
		"name": name,
	}, tagging)

	ctx := testctx.RunSingleExample(t, "../../examples", "with-alb", testctx.TestConfig{
		Name:      name,
		ExtraVars: extraVars,
	})

	t.Run("HttpsListenerArnMatchesPattern", func(t *testing.T) {
		listenerArn := terraform.Output(t, ctx.Terraform, "https_listener_arn")
		assert.NotEmpty(t, listenerArn,
			"https_listener_arn must be non-empty when an HTTPS listener is configured")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:elasticloadbalancing:`),
			listenerArn,
			"https_listener_arn must match ^arn:aws:elasticloadbalancing: (AC-9)")
	})

	t.Run("TargetGroupArnsNonEmpty", func(t *testing.T) {
		tgArnsJSON := terraform.OutputJson(t, ctx.Terraform, "target_group_arns")
		require.NotEmpty(t, tgArnsJSON, "target_group_arns output must be non-empty JSON")

		var tgArns map[string]interface{}
		err := json.Unmarshal([]byte(tgArnsJSON), &tgArns)
		require.NoError(t, err, "target_group_arns must be valid JSON map")

		assert.NotEmpty(t, tgArns,
			"target_group_arns map must be non-empty when ALB target groups are configured (AC-9)")
	})

	t.Run("AutoscalingTargetPresent", func(t *testing.T) {
		autoscalingTarget := terraform.Output(t, ctx.Terraform, "autoscaling_target_resource_id")
		assert.NotEmpty(t, autoscalingTarget,
			"autoscaling_target_resource_id must be non-empty when autoscaling is enabled (AC-9)")
	})

	t.Run("SsmParameterArnsMapSizeFour", func(t *testing.T) {
		arnsJSON := terraform.OutputJson(t, ctx.Terraform, "ssm_parameter_arns")
		require.NotEmpty(t, arnsJSON, "ssm_parameter_arns output must be non-empty JSON")

		var arns map[string]interface{}
		err := json.Unmarshal([]byte(arnsJSON), &arns)
		require.NoError(t, err, "ssm_parameter_arns must be valid JSON map")

		// The with-alb example configures 4 SSM parameters per docs/terragrunt-concepts.md inventory:
		// adot-config, waf-rate-limit, otlp-max-body-bytes, public-client-id.
		assert.Equal(t, 4, len(arns),
			"ssm_parameter_arns map must contain exactly 4 entries matching the with-alb example SSM inventory (AC-9)")
	})

	t.Run("AdotConfigParameterIsSecureString", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")
		require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty output")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
			"terraform show -json output must be valid JSON")

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok, "state JSON must contain a 'values' object")

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok, "state values must contain a 'root_module' object")

		paramType, kmsKeyID := findAdotConfigParameter(t, rootModule)
		assert.Equal(t, "SecureString", paramType,
			"adot-config SSM parameter must be of type SecureString per docs/terragrunt-concepts.md (AC-3)")
		assert.NotEmpty(t, kmsKeyID,
			"adot-config SSM parameter must have a non-null kms_key_id per docs/terragrunt-concepts.md (AC-3)")
	})

	t.Run("AlbDnsNameNonEmpty", func(t *testing.T) {
		dnsName := terraform.Output(t, ctx.Terraform, "alb_dns_name")
		assert.NotEmpty(t, dnsName,
			"alb_dns_name must be non-empty when an ALB is configured (AC-9)")
	})

	// LoadBalancerContainerNameExistsInTaskDefinition asserts the ECS service's
	// load_balancer.container_name resolves to a container that actually exists in
	// the task definition's container_definitions. A mismatch (the load_balancer
	// referencing a container name absent from the task definition) makes
	// CreateService fail at apply with InvalidParameterException
	// ("The container <name> does not exist in the task definition"). The
	// ecs-app-deploy reference wires container_name = var.service_name, so the
	// caller-supplied container_definitions MUST name their container after
	// service_name; this test enforces that invariant on the rendered state.
	t.Run("LoadBalancerContainerNameExistsInTaskDefinition", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")
		require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty output")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
			"terraform show -json output must be valid JSON")

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok, "state JSON must contain a 'values' object")

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok, "state values must contain a 'root_module' object")

		lbContainerNames := collectEcsServiceLoadBalancerContainerNames(t, rootModule)
		require.NotEmpty(t, lbContainerNames,
			"at least one aws_ecs_service with a load_balancer block must exist in state")

		taskContainerNames := collectTaskDefinitionContainerNames(t, rootModule)
		require.NotEmpty(t, taskContainerNames,
			"at least one aws_ecs_task_definition with container_definitions must exist in state")

		for _, lbName := range lbContainerNames {
			assert.Contains(t, taskContainerNames, lbName,
				"ECS service load_balancer.container_name %q must match a container name in the task definition (containers: %v); a mismatch fails CreateService with InvalidParameterException",
				lbName, taskContainerNames)
		}
	})

	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (idempotency requirement)")
	})
}

// findAdotConfigParameter walks the Terraform state module tree and returns the type and
// kms_key_id of the first aws_ssm_parameter resource whose name contains "adot-config".
func findAdotConfigParameter(t *testing.T, module map[string]interface{}) (paramType string, kmsKeyID string) {
	t.Helper()

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType == "aws_ssm_parameter" {
				vals, ok := res["values"].(map[string]interface{})
				if !ok {
					continue
				}
				name, _ := vals["name"].(string)
				if strings.Contains(name, "adot-config") {
					pt, _ := vals["type"].(string)
					kms, _ := vals["key_id"].(string)
					return pt, kms
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if pt, kms := findAdotConfigParameter(t, child); pt != "" {
				return pt, kms
			}
		}
	}

	return "", ""
}

// collectEcsServiceLoadBalancerContainerNames walks the Terraform state module tree
// and returns the container_name from every aws_ecs_service.load_balancer block found.
func collectEcsServiceLoadBalancerContainerNames(t *testing.T, module map[string]interface{}) []string {
	t.Helper()

	names := make([]string, 0)

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_ecs_service" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			loadBalancers, ok := vals["load_balancer"].([]interface{})
			if !ok {
				continue
			}
			for _, rawLB := range loadBalancers {
				lb, ok := rawLB.(map[string]interface{})
				if !ok {
					continue
				}
				if name, ok := lb["container_name"].(string); ok && name != "" {
					names = append(names, name)
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			names = append(names, collectEcsServiceLoadBalancerContainerNames(t, child)...)
		}
	}

	return names
}

// collectTaskDefinitionContainerNames walks the Terraform state module tree and returns
// the "name" of every container declared in every aws_ecs_task_definition's
// container_definitions JSON document found.
func collectTaskDefinitionContainerNames(t *testing.T, module map[string]interface{}) []string {
	t.Helper()

	names := make([]string, 0)

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_ecs_task_definition" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			defsJSON, ok := vals["container_definitions"].(string)
			if !ok || defsJSON == "" {
				continue
			}
			var defs []map[string]interface{}
			if err := json.Unmarshal([]byte(defsJSON), &defs); err != nil {
				t.Fatalf("aws_ecs_task_definition.container_definitions must be a valid JSON array: %v", err)
			}
			for _, def := range defs {
				if name, ok := def["name"].(string); ok && name != "" {
					names = append(names, name)
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			names = append(names, collectTaskDefinitionContainerNames(t, child)...)
		}
	}

	return names
}

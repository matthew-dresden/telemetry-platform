//go:build terratest

package basic_test

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
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

// TestBasicWAFWebACLRequiredOutputsNotEmpty asserts all required outputs are non-empty.
func TestBasicWAFWebACLRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("test-waf-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "web_acl_arn"), "web_acl_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "web_acl_id"), "web_acl_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "web_acl_name"), "web_acl_name must not be empty")

	// Per-sub-rule rule_action_override coverage (folded into this apply to avoid a
	// redundant WAFv2 apply/destroy cycle): the basic example overrides
	// AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY sub-rule to count while the
	// group override_action stays none. A successful apply proves WAFv2 accepts the
	// rendered managed_rule_group_statement rule_action_override block, and the output
	// assertion guards the wiring against regression.
	overridesJSON := terraform.OutputJson(t, ctx.Terraform, "rule_action_overrides")
	require.NotEmpty(t, overridesJSON, "rule_action_overrides output must not be empty")

	var overrides map[string]map[string]string
	require.NoError(t, json.Unmarshal([]byte(overridesJSON), &overrides),
		"rule_action_overrides output must be a JSON object of group -> { sub-rule -> action }")

	common, ok := overrides["AWSManagedRulesCommonRuleSet"]
	require.True(t, ok, "rule_action_overrides must include the AWSManagedRulesCommonRuleSet group")
	assert.Equal(t, "count", common["SizeRestrictions_BODY"],
		"AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY sub-rule must be overridden to count "+
			"so oversize request bodies are not blocked while the rest of the group stays enforced")
}

// TestBasicWAFWebACLARNFormat asserts the web_acl_arn output matches the WAFv2 ARN pattern.
func TestBasicWAFWebACLARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("test-waf-%s", suffix),
		}, mustTaggingVars(t)),
	})

	webAclArn := terraform.Output(t, ctx.Terraform, "web_acl_arn")
	assert.Regexp(t, `^arn:aws:wafv2:`, webAclArn, "web_acl_arn must match WAFv2 ARN pattern ^arn:aws:wafv2:")
}

// TestBasicWAFWebACLResourceCount asserts exactly one aws_wafv2_web_acl resource is created.
func TestBasicWAFWebACLResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("test-waf-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_wafv2_web_acl", 1)
	assertions.AssertResourceCount(t, ctx, "aws_wafv2_web_acl_logging_configuration", 0)
}

// TestBasicWAFWebACLOwnsLogGroup asserts that when create_log_group = true the module
// creates its OWN CloudWatch log group as the WAFv2 logging destination (docs/terragrunt-concepts.md):
// exactly one aws_cloudwatch_log_group and one aws_wafv2_web_acl_logging_configuration,
// and the log group name uses the AWS-required aws-waf-logs- prefix.
func TestBasicWAFWebACLOwnsLogGroup(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	name := fmt.Sprintf("test-waf-lg-%s", suffix)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-loggroup-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name":             name,
			"logging_enabled":  true,
			"create_log_group": true,
			"log_kms_key_arn":  os.Getenv("WAF_LOG_KMS_KEY_ARN"),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 1)
	assertions.AssertResourceCount(t, ctx, "aws_wafv2_web_acl_logging_configuration", 1)

	logGroupName := terraform.Output(t, ctx.Terraform, "log_group_name")
	assert.Regexp(t, `^aws-waf-logs-`, logGroupName,
		"WAFv2 CloudWatch log group name must start with the AWS-required aws-waf-logs- prefix (docs/terragrunt-concepts.md)")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "log_group_arn"),
		"log_group_arn must be non-empty when create_log_group is true")
}

// TestBasicWAFWebACLCreateLogGroupRejectsExternalDestination asserts the fail-fast
// mutual-exclusion guard: create_log_group = true with a non-empty log_destination_arns
// fails at plan time (the module-owned group is the sole destination, docs/terragrunt-concepts.md).
func TestBasicWAFWebACLCreateLogGroupRejectsExternalDestination(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-loggroup-conflict-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":                 fmt.Sprintf("test-waf-%s", suffix),
			"logging_enabled":      true,
			"create_log_group":     true,
			"log_kms_key_arn":      "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000",
			"log_destination_arns": []string{"arn:aws:logs:us-east-1:000000000000:log-group:external"},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when create_log_group is true and log_destination_arns is non-empty")
}

// TestBasicWAFWebACLInvalidScope asserts that an invalid scope fails validation.
func TestBasicWAFWebACLInvalidScope(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-bad-scope-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":  fmt.Sprintf("test-waf-%s", suffix),
			"scope": "INVALID",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when scope is not CLOUDFRONT or REGIONAL")
}

// TestBasicWAFWebACLInvalidDefaultAction asserts that an invalid default_action fails validation.
func TestBasicWAFWebACLInvalidDefaultAction(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-bad-action-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":           fmt.Sprintf("test-waf-%s", suffix),
			"default_action": "INVALID",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when default_action is not allow or block")
}

// TestBasicWAFWebACLLoggingEnabledWithNullKMSKeyFails asserts that enabling logging
// without supplying a log_kms_key_arn fails at plan time (decision D2 enforcement).
// log_destination_arns is intentionally omitted to match the portal call-site pattern
// (portal does not pass log_destination_arns); the D2 validation fires on log_kms_key_arn alone.
func TestBasicWAFWebACLLoggingEnabledWithNullKMSKeyFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-waf-logging-no-kms-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":            fmt.Sprintf("test-waf-%s", suffix),
			"logging_enabled": true,
			"log_kms_key_arn": nil,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when logging_enabled is true and log_kms_key_arn is null")
}

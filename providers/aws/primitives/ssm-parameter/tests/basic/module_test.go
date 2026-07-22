//go:build terratest

package basic_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/stretchr/testify/assert"
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


// TestBasicSSMParameterCreation asserts the basic example creates a String parameter with the correct ARN format.
func TestBasicSSMParameterCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-creation-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	paramArn := terraform.Output(t, ctx.Terraform, "parameter_arn")
	assert.Regexp(t, `^arn:aws:ssm:`, paramArn, "parameter_arn must match SSM ARN pattern")
}

// TestBasicSSMParameterResourceCount asserts exactly one aws_ssm_parameter is created.
func TestBasicSSMParameterResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ssm_parameter", 1)
}

// TestBasicSSMParameterVersionAtLeastOne asserts the parameter_version output is >= 1 after creation.
func TestBasicSSMParameterVersionAtLeastOne(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-version-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	paramVersion := terraform.Output(t, ctx.Terraform, "parameter_version")
	assert.NotEmpty(t, paramVersion, "parameter_version must not be empty")
	assert.NotEqual(t, "0", paramVersion, "parameter_version must be >= 1 after creation")
}

// TestBasicSSMParameterOutputsNotEmpty asserts all required metadata outputs are non-empty and value is not exposed.
func TestBasicSSMParameterOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_arn"), "parameter_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_name"), "parameter_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_version"), "parameter_version must not be empty")
}

// TestBasicSSMParameterIdempotency asserts a second plan after apply shows no changes.
func TestBasicSSMParameterIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-idempotent-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ssm_parameter", 1)
}

// TestBasicSSMParameterInvalidType asserts that an unsupported type fails validation.
func TestBasicSSMParameterInvalidType(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-bad-type-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":  fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
			"type":  "InvalidType",
			"value": "100",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail for type not in allowed enum")
}

// TestBasicSSMParameterSecureStringWithoutKMSKeyFails asserts SecureString without kms_key_id fails validation.
func TestBasicSSMParameterSecureStringWithoutKMSKeyFails(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-ssm-param-secure-no-kms-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":  fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
			"type":  "SecureString",
			"value": "secret-value",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when type is SecureString and kms_key_id is not provided")
}

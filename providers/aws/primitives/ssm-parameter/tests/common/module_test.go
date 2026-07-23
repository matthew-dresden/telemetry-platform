//go:build terratest

package common_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
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

// TestCommonSSMParameterTerraformVersion asserts the Terraform version satisfies the pinned constraint.
func TestCommonSSMParameterTerraformVersion(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-tf-version-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonSSMParameterRequiredOutputsBasic asserts all required metadata outputs exist for the basic example.
func TestCommonSSMParameterRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_arn"), "parameter_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_name"), "parameter_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_version"), "parameter_version must not be empty")
}

// TestCommonSSMParameterRequiredOutputsSecureString asserts all required metadata outputs exist for the securestring example.
// This test MUST NOT read the secret value -- it asserts only metadata outputs.
func TestCommonSSMParameterRequiredOutputsSecureString(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-outputs-secure-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_arn"), "parameter_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_name"), "parameter_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "parameter_version"), "parameter_version must not be empty")
}

// TestCommonSSMParameterValidateBasic asserts the basic example passes terraform validate.
func TestCommonSSMParameterValidateBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-validate-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	paramArn := terraform.Output(t, ctx.Terraform, "parameter_arn")
	assert.NotEmpty(t, paramArn, "parameter_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonSSMParameterValidateSecureString asserts the securestring example passes terraform validate.
func TestCommonSSMParameterValidateSecureString(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-validate-secure-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	paramArn := terraform.Output(t, ctx.Terraform, "parameter_arn")
	assert.NotEmpty(t, paramArn, "parameter_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonSSMParameterIdempotencyBasic asserts the basic example is idempotent.
func TestCommonSSMParameterIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-idempotent-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/qa/ingest/waf-rate-limit-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ssm_parameter", 1)
}

// TestCommonSSMParameterIdempotencySecureString asserts the securestring example is idempotent.
func TestCommonSSMParameterIdempotencySecureString(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("common-ssm-param-idempotent-secure-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ssm_parameter", 1)
}

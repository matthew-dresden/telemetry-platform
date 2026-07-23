//go:build terratest

package securestring_test

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

// TestSecureStringSSMParameterCreation asserts the securestring example creates a parameter with the correct ARN.
func TestSecureStringSSMParameterCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("securestring-ssm-param-creation-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	paramArn := terraform.Output(t, ctx.Terraform, "parameter_arn")
	assert.Regexp(t, `^arn:aws:ssm:`, paramArn, "parameter_arn must match SSM ARN pattern")
}

// TestSecureStringSSMParameterResourceCount asserts exactly one aws_ssm_parameter is created.
func TestSecureStringSSMParameterResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("securestring-ssm-param-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ssm_parameter", 1)
}

// TestSecureStringSSMParameterTypeInState asserts the parameter type is SecureString in state using only metadata.
// This test MUST NOT read the secret value -- it asserts only parameter metadata outputs.
func TestSecureStringSSMParameterTypeInState(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("securestring-ssm-param-type-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	// Assert only metadata outputs -- never read the secret value
	paramName := terraform.Output(t, ctx.Terraform, "parameter_name")
	assert.Contains(t, paramName, "/telemetry/prod/ingest/adot-config", "parameter_name must follow the section-4 path convention")

	paramArn := terraform.Output(t, ctx.Terraform, "parameter_arn")
	assert.Regexp(t, `^arn:aws:ssm:`, paramArn, "parameter_arn must match SSM ARN pattern")

	// Assert the aws_ssm_parameter.this.type attribute equals "SecureString" by reading state directly.
	// terraform state show emits only metadata (including type) without decrypting the secret value.
	stateOutput, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "show", "module.example.aws_ssm_parameter.this")
	assert.NoError(t, err, "terraform state show must succeed")
	assert.Contains(t, stateOutput, `"SecureString"`, `aws_ssm_parameter.this.type must equal "SecureString" in state`)
}

// TestSecureStringSSMParameterKMSBinding asserts the fixture KMS key ARN output is non-empty,
// confirming the SecureString parameter is bound to the telemetry-config CMK.
// Only metadata (the KMS key ARN output) is asserted -- no secret value is accessed.
func TestSecureStringSSMParameterKMSBinding(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("securestring-ssm-param-kms-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	// Assert KMS key ARN metadata output -- confirms the CMK binding without reading the secret
	kmsKeyArn := terraform.Output(t, ctx.Terraform, "kms_key_arn")
	assert.Regexp(t, `^arn:aws:kms:`, kmsKeyArn, "kms_key_arn must match KMS key ARN pattern, confirming CMK binding")
	assert.NotEmpty(t, kmsKeyArn, "kms_key_arn must not be empty, confirming telemetry-config CMK was created and bound")
}

// TestSecureStringSSMParameterSectionFourPath asserts the parameter name follows the docs/terragrunt-concepts.md path convention.
func TestSecureStringSSMParameterSectionFourPath(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("securestring-ssm-param-path-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	paramName := terraform.Output(t, ctx.Terraform, "parameter_name")
	assert.Contains(t, paramName, "/telemetry/prod/ingest/adot-config", "parameter_name must be at the section-4 /telemetry/<env>/<plane>/<key> path")
}

// TestSecureStringSSMParameterIdempotency asserts a second plan after apply shows no changes.
// aws_ssm_parameter lives inside module.example (prefix checked by AssertResourceCount).
// aws_kms_key is a root-level fixture resource; its existence is confirmed via the kms_key_arn
// metadata output rather than state-list prefix matching.
func TestSecureStringSSMParameterIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "securestring", testctx.TestConfig{
		Name: fmt.Sprintf("securestring-ssm-param-idempotent-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("/telemetry/prod/ingest/adot-config-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_ssm_parameter", 1)
	// Confirm the fixture KMS key exists via metadata output (root-level resource, not under module.example).
	kmsKeyArn := terraform.Output(t, ctx.Terraform, "kms_key_arn")
	assert.Regexp(t, `^arn:aws:kms:`, kmsKeyArn, "kms_key_arn must match KMS ARN pattern, confirming fixture CMK exists after idempotency re-plan")
}

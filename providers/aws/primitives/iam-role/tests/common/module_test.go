//go:build terratest

package common_test

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

// TestCommonIAMRoleTerraformVersion asserts the Terraform version satisfies the pinned constraint.
func TestCommonIAMRoleTerraformVersion(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-tf-version-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-version-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonIAMRoleRequiredOutputsBasic asserts all required outputs are present for the basic example.
func TestCommonIAMRoleRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-out-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_arn"), "role_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_name"), "role_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_id"), "role_id must not be empty")
}

// TestCommonIAMRoleRequiredOutputsWithInline asserts all required outputs are present for the with-inline example.
func TestCommonIAMRoleRequiredOutputsWithInline(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-outputs-inline-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-out-inline-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_arn"), "role_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_name"), "role_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_id"), "role_id must not be empty")
}

// TestCommonIAMRoleValidateBasic asserts the basic example passes terraform validate.
func TestCommonIAMRoleValidateBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-validate-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-validate-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	roleArn := terraform.Output(t, ctx.Terraform, "role_arn")
	assert.NotEmpty(t, roleArn, "role_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonIAMRoleValidateWithInline asserts the with-inline example passes terraform validate.
func TestCommonIAMRoleValidateWithInline(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-validate-inline-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-validate-inline-%s", suffix),
		}, mustTaggingVars(t)),
	})

	roleArn := terraform.Output(t, ctx.Terraform, "role_arn")
	assert.NotEmpty(t, roleArn, "role_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonIAMRoleIdempotencyBasic asserts the basic example is idempotent.
// The framework's RunSingleExample enforces idempotency when TERRATEST_IDEMPOTENCY=true;
// this test additionally verifies idempotency via AssertIdempotent.
func TestCommonIAMRoleIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-idempotent-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-idem-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}

// TestCommonIAMRoleIdempotencyWithInline asserts the with-inline example is idempotent.
// The framework's RunSingleExample enforces idempotency when TERRATEST_IDEMPOTENCY=true;
// this test additionally verifies idempotency via AssertIdempotent.
func TestCommonIAMRoleIdempotencyWithInline(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("common-iam-role-idempotent-inline-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-idem-inline-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}

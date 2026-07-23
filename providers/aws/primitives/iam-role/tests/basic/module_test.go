//go:build terratest

package basic_test

import (
	"fmt"
	"os"
	"regexp"
	"strings"
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

// countStateResources returns the number of resources in terraform state whose address
// matches the given resourceType under the module.example namespace.
// The basic example instantiates the module under test in a block named "example", so
// state addresses are of the form module.example.<resourceType>.<name>.
func countStateResources(t *testing.T, ctx testctx.TestContext, resourceType string) int {
	t.Helper()
	output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
	assert.NoError(t, err, "terraform state list must not fail")
	pattern := regexp.MustCompile(fmt.Sprintf(`module\.example\.%s\.`, regexp.QuoteMeta(resourceType)))
	count := 0
	for _, line := range strings.Split(output, "\n") {
		if pattern.MatchString(line) {
			count++
		}
	}
	return count
}

// TestBasicIAMRoleCreation asserts that the basic example creates an IAM role with the correct ARN format.
func TestBasicIAMRoleCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-iam-role-creation-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	roleArn := terraform.Output(t, ctx.Terraform, "role_arn")
	assert.Regexp(t, `^arn:aws:iam::[0-9]{12}:role/`, roleArn, "role_arn must match IAM role ARN pattern")
}

// TestBasicIAMRoleResourceCounts asserts exactly one aws_iam_role and one aws_iam_role_policy_attachment exist.
func TestBasicIAMRoleResourceCounts(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-iam-role-counts-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-counts-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.Equal(t, 1, countStateResources(t, ctx, "aws_iam_role"), "exactly 1 aws_iam_role must be in state")
	assert.Equal(t, 1, countStateResources(t, ctx, "aws_iam_role_policy_attachment"), "exactly 1 aws_iam_role_policy_attachment must be in state")
}

// TestBasicIAMRoleOutputsNotEmpty asserts all required outputs are non-empty.
func TestBasicIAMRoleOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-iam-role-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-outputs-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_arn"), "role_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_name"), "role_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "role_id"), "role_id must not be empty")
}

// TestBasicIAMRoleIdempotency asserts that a second plan after apply shows no changes.
// The framework's RunSingleExample already enforces idempotency when TERRATEST_IDEMPOTENCY=true;
// this test additionally verifies idempotency via AssertIdempotent.
func TestBasicIAMRoleIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-iam-role-idempotent-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-idem-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}

// TestBasicIAMRoleMalformedTrustPolicy asserts that a malformed assume_role_policy_json fails validation.
func TestBasicIAMRoleMalformedTrustPolicy(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-iam-role-bad-trust-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":                    fmt.Sprintf("telemetry-bad-trust-%s", suffix),
			"assume_role_policy_json": "not-valid-json{{{",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail for malformed assume_role_policy_json")
}

// TestBasicIAMRoleNameTooLong asserts that a role name exceeding 64 characters fails validation.
func TestBasicIAMRoleNameTooLong(t *testing.T) {
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: "basic-iam-role-name-too-long",
		ExtraVars: map[string]interface{}{
			"name": "this-role-name-is-way-too-long-and-exceeds-the-64-character-iam-limit-abc",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when role name exceeds 64 characters")
}

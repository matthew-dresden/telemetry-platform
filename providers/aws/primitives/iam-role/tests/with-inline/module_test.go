//go:build terratest

package withinline_test

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
// The with-inline example instantiates the module under test in a block named "example",
// so state addresses are of the form module.example.<resourceType>.<name>.
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

// TestWithInlineIAMRoleCreation asserts the with-inline example creates a role with the correct ARN format.
func TestWithInlineIAMRoleCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("with-inline-iam-role-creation-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-firehose-%s", suffix),
		}, mustTaggingVars(t)),
	})

	roleArn := terraform.Output(t, ctx.Terraform, "role_arn")
	assert.Regexp(t, `^arn:aws:iam::[0-9]{12}:role/`, roleArn, "role_arn must match IAM role ARN pattern")
}

// TestWithInlineIAMRolePolicyCount asserts exactly two aws_iam_role_policy inline policies are created.
func TestWithInlineIAMRolePolicyCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("with-inline-iam-role-policy-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-inline-count-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.Equal(t, 2, countStateResources(t, ctx, "aws_iam_role_policy"), "exactly 2 aws_iam_role_policy resources must be in state")
}

// TestWithInlineIAMRoleIdempotency asserts a second plan after apply shows no changes.
// The framework's RunSingleExample already enforces idempotency when TERRATEST_IDEMPOTENCY=true;
// this test additionally verifies idempotency via AssertIdempotent.
func TestWithInlineIAMRoleIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("with-inline-iam-role-idempotent-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("telemetry-inline-idem-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertIdempotent(t, ctx)
}

// TestWithInlineIAMRoleMalformedInlinePolicy asserts that a malformed inline policy value fails validation.
func TestWithInlineIAMRoleMalformedInlinePolicy(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/with-inline", testctx.TestConfig{
		Name: fmt.Sprintf("with-inline-iam-role-bad-inline-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name": fmt.Sprintf("telemetry-bad-inline-%s", suffix),
			"inline_policies": map[string]interface{}{
				"bad-policy": "not-valid-json{{{",
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when an inline policy value is not valid JSON")
}

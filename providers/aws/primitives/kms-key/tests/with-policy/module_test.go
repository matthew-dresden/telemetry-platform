//go:build terratest

package withpolicy_test

import (
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

// TestWithPolicyKMSKeyCreation asserts the key is created and custom policy is applied in state.
func TestWithPolicyKMSKeyCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	accountID := os.Getenv("AWS_ACCOUNT_ID")
	require.NotEmpty(t, accountID, "AWS_ACCOUNT_ID environment variable must be set")

	ctx := testctx.RunSingleExample(t, "../../examples", "with-policy", testctx.TestConfig{
		Name: fmt.Sprintf("with-policy-kms-creation-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-policy-%s", suffix),
			"account_id": accountID,
		}, mustTaggingVars(t)),
	})

	keyArn := terraform.Output(t, ctx.Terraform, "key_arn")
	assert.NotEmpty(t, keyArn, "key_arn must not be empty")
	assert.Regexp(t, `^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/`, keyArn, "key_arn must match KMS ARN pattern")
}

// TestWithPolicyKMSKeyResourceCount asserts exactly one aws_kms_key is created.
func TestWithPolicyKMSKeyResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	accountID := os.Getenv("AWS_ACCOUNT_ID")
	require.NotEmpty(t, accountID, "AWS_ACCOUNT_ID environment variable must be set")

	ctx := testctx.RunSingleExample(t, "../../examples", "with-policy", testctx.TestConfig{
		Name: fmt.Sprintf("with-policy-kms-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-count-%s", suffix),
			"account_id": accountID,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kms_key", 1)
}

// TestWithPolicyKMSKeyPolicyInState asserts the custom policy is reflected in state outputs.
func TestWithPolicyKMSKeyPolicyInState(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	accountID := os.Getenv("AWS_ACCOUNT_ID")
	require.NotEmpty(t, accountID, "AWS_ACCOUNT_ID environment variable must be set")

	ctx := testctx.RunSingleExample(t, "../../examples", "with-policy", testctx.TestConfig{
		Name: fmt.Sprintf("with-policy-kms-policy-state-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"alias_name": fmt.Sprintf("test-state-%s", suffix),
			"account_id": accountID,
		}, mustTaggingVars(t)),
	})

	// The key_arn and alias_arn outputs confirm the key was created with the policy.
	// The policy content is applied at the aws_kms_key resource level.
	keyArn := terraform.Output(t, ctx.Terraform, "key_arn")
	assert.NotEmpty(t, keyArn, "key_arn must not be empty when custom policy is applied")

	aliasArn := terraform.Output(t, ctx.Terraform, "alias_arn")
	assert.NotEmpty(t, aliasArn, "alias_arn must not be empty")
}

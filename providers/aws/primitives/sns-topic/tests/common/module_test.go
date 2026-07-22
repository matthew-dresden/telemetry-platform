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

// TestCommonSNSTopicTerraformVersionBasic asserts the Terraform version satisfies the pinned constraint for the basic example.
func TestCommonSNSTopicTerraformVersionBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-tf-version-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-common-ver-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonSNSTopicTerraformVersionWithSubscriptions asserts the Terraform version satisfies the pinned constraint for the with-subscriptions example.
func TestCommonSNSTopicTerraformVersionWithSubscriptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-tf-version-ws-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-common-ver-ws-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonSNSTopicRequiredOutputsBasic asserts both required outputs (topic_arn, topic_name) are present for the basic example.
func TestCommonSNSTopicRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	topicName := fmt.Sprintf("test-common-out-basic-%s", suffix)

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-outputs-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": topicName,
		}, mustTaggingVars(t)),
	})

	topicArn := terraform.Output(t, ctx.Terraform, "topic_arn")
	assert.Regexp(t, `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`, topicArn, "topic_arn must match SNS ARN pattern")

	outputTopicName := terraform.Output(t, ctx.Terraform, "topic_name")
	assert.NotEmpty(t, outputTopicName, "topic_name must not be empty")
	assert.Equal(t, topicName, outputTopicName, "topic_name must equal the provisioned topic name")
}

// TestCommonSNSTopicRequiredOutputsWithSubscriptions asserts both required outputs are present for the with-subscriptions example.
func TestCommonSNSTopicRequiredOutputsWithSubscriptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	topicName := fmt.Sprintf("test-common-out-ws-%s", suffix)

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-outputs-ws-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": topicName,
		}, mustTaggingVars(t)),
	})

	topicArn := terraform.Output(t, ctx.Terraform, "topic_arn")
	assert.Regexp(t, `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`, topicArn, "topic_arn must match SNS ARN pattern")

	outputTopicName := terraform.Output(t, ctx.Terraform, "topic_name")
	assert.NotEmpty(t, outputTopicName, "topic_name must not be empty")
	assert.Equal(t, topicName, outputTopicName, "topic_name must equal the provisioned topic name")
}

// TestCommonSNSTopicValidateBasic asserts the basic example passes terraform validate (confirmed via successful apply).
func TestCommonSNSTopicValidateBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-validate-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-common-val-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	topicArn := terraform.Output(t, ctx.Terraform, "topic_arn")
	assert.NotEmpty(t, topicArn, "topic_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonSNSTopicValidateWithSubscriptions asserts the with-subscriptions example passes terraform validate.
func TestCommonSNSTopicValidateWithSubscriptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-validate-ws-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-common-val-ws-%s", suffix),
		}, mustTaggingVars(t)),
	})

	topicArn := terraform.Output(t, ctx.Terraform, "topic_arn")
	assert.NotEmpty(t, topicArn, "topic_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonSNSTopicIdempotencyBasic asserts the basic example is idempotent.
func TestCommonSNSTopicIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-idem-basic-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-common-idem-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_subscription", 0)
}

// TestCommonSNSTopicIdempotencyWithSubscriptions asserts the with-subscriptions example is idempotent.
func TestCommonSNSTopicIdempotencyWithSubscriptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("common-sns-idem-ws-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-common-idem-ws-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_subscription", 1)
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_policy", 1)
}

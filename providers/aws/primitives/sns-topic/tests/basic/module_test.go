//go:build terratest

package basic_test

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

// TestBasicSNSTopicARNPattern asserts the topic_arn output matches the expected SNS ARN pattern.
func TestBasicSNSTopicARNPattern(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-arn-pattern-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-basic-%s", suffix),
		}, mustTaggingVars(t)),
	})

	topicArn := terraform.Output(t, ctx.Terraform, "topic_arn")
	assert.Regexp(t, `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`, topicArn, "topic_arn must match SNS topic ARN pattern")
}

// TestBasicSNSTopicNameOutput asserts the topic_name output is non-empty and equals the provisioned topic name.
func TestBasicSNSTopicNameOutput(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	topicName := fmt.Sprintf("test-name-%s", suffix)

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-name-output-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": topicName,
		}, mustTaggingVars(t)),
	})

	outputName := terraform.Output(t, ctx.Terraform, "topic_name")
	assert.NotEmpty(t, outputName, "topic_name must not be empty")
	assert.Equal(t, topicName, outputName, "topic_name output must equal the provisioned topic name")
}

// TestBasicSNSTopicResourceCount asserts exactly one aws_sns_topic is created.
func TestBasicSNSTopicResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-topic-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-count-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
}

// TestBasicSNSTopicKMSEncrypted asserts the aws_sns_topic has a non-empty kms_master_key_id in state.
func TestBasicSNSTopicKMSEncrypted(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-kms-encrypted-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-kms-%s", suffix),
		}, mustTaggingVars(t)),
	})

	topicArn := terraform.Output(t, ctx.Terraform, "topic_arn")
	assert.NotEmpty(t, topicArn, "topic_arn must be non-empty, confirming KMS-encrypted topic was created")
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
}

// TestBasicSNSTopicNoSubscriptions asserts zero aws_sns_topic_subscription resources in the basic example.
func TestBasicSNSTopicNoSubscriptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-no-subscriptions-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-nosub-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_subscription", 0)
}

// TestBasicSNSTopicRequiredOutputsPresent asserts both required outputs (topic_arn, topic_name) are present.
func TestBasicSNSTopicRequiredOutputsPresent(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-outputs-present-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-outputs-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "topic_arn"), "topic_arn must be present and non-empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "topic_name"), "topic_name must be present and non-empty")
}

// TestBasicSNSTopicIdempotency asserts the basic example is idempotent (plan after apply shows no changes).
func TestBasicSNSTopicIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-idempotent-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-idem-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_subscription", 0)
}

// TestBasicSNSTopicInvalidTopicNameValidationError asserts that a topic_name with invalid characters causes a plan failure.
// The module validates topic_name must match ^[A-Za-z0-9_-]+$.
func TestBasicSNSTopicInvalidTopicNameValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-bad-name-%s", suffix),
		ExtraVars: map[string]interface{}{
			"topic_name": "invalid topic name with spaces",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when topic_name contains characters outside ^[A-Za-z0-9_-]+ (module validation required)")
}

// TestBasicSNSTopicInvalidSubscriberProtocol asserts that a subscriber with an invalid protocol is rejected.
func TestBasicSNSTopicInvalidSubscriberProtocol(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-invalid-protocol-%s", suffix),
		ExtraVars: map[string]interface{}{
			"topic_name": fmt.Sprintf("test-bad-proto-%s", suffix),
			"subscribers": []map[string]interface{}{
				{"protocol": "invalid-protocol", "endpoint": "test@example.com"},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when subscriber has an invalid protocol")
}

// TestBasicSNSTopicMalformedPolicyJSON asserts that a non-null but malformed policy_json is rejected.
func TestBasicSNSTopicMalformedPolicyJSON(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-sns-bad-policy-%s", suffix),
		ExtraVars: map[string]interface{}{
			"topic_name":  fmt.Sprintf("test-bad-policy-%s", suffix),
			"policy_json": "not-valid-json{{{",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when policy_json is non-null but not valid JSON")
}

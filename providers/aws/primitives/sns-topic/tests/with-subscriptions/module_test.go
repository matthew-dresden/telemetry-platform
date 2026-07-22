//go:build terratest

package withsubscriptions_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
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

// TestWithSubscriptionsSNSTopicSubscriptionCount asserts at least one aws_sns_topic_subscription exists.
func TestWithSubscriptionsSNSTopicSubscriptionCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("ws-sns-sub-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-ws-sub-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_subscription", 1)
}

// TestWithSubscriptionsSNSTopicPolicyCount asserts exactly one aws_sns_topic_policy exists.
func TestWithSubscriptionsSNSTopicPolicyCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("ws-sns-policy-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-ws-policy-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_policy", 1)
}

// TestWithSubscriptionsSNSTopicIdempotency asserts the with-subscriptions example is idempotent.
func TestWithSubscriptionsSNSTopicIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-subscriptions", testctx.TestConfig{
		Name: fmt.Sprintf("ws-sns-idempotent-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"topic_name": fmt.Sprintf("test-ws-idem-%s", suffix),
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_subscription", 1)
	assertions.AssertResourceCount(t, ctx, "aws_sns_topic_policy", 1)
}

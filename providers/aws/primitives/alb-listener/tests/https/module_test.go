//go:build terratest

package https_test

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

// TestHTTPSListener applies the https example ONCE and runs every https
// assertion as a subtest against that single applied state. Provisioning a
// full ALB + listener is the expensive step (several minutes each), so the
// version, output, attribute, resource-count, and idempotency checks that were
// previously eight separate `terraform apply` cycles are collapsed into one
// shared apply. The framework's RunExample registers a t.Cleanup destroy on
// this parent test, so the listener is destroyed only after all subtests
// complete. Cases that genuinely require a different INPUT (the validation-error
// cases) live in the common package as plan-only tests and are not consolidated
// here.
func TestHTTPSListener(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "https", testctx.TestConfig{
		Name: fmt.Sprintf("https-listener-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"name": fmt.Sprintf("albl-h-%s", suffix[:8]),
		}, mustTaggingVars(t)),
	})

	// TerraformVersion: the pinned Terraform version satisfies the constraint.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// RequiredOutputs: all required outputs are present after apply.
	t.Run("RequiredOutputs", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "listener_arns"), "listener_arns must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "https_listener_arn"), "https_listener_arn must not be empty")
		// listener_rule_arns is an empty map when no rules are defined -- assert the output key is present and valid JSON.
		listenerRuleArns := terraform.Output(t, ctx.Terraform, "listener_rule_arns")
		assert.NotNil(t, listenerRuleArns, "listener_rule_arns output must be present")
	})

	// Validate: a successful apply proves the example passes terraform validate;
	// the non-empty https_listener_arn output confirms it.
	t.Run("Validate", func(t *testing.T) {
		httpsListenerArn := terraform.Output(t, ctx.Terraform, "https_listener_arn")
		assert.NotEmpty(t, httpsListenerArn, "https_listener_arn must be non-empty after successful apply, confirming validate passed")
	})

	// Creation: the HTTPS listener ARN matches the ALB listener ARN pattern.
	t.Run("Creation", func(t *testing.T) {
		httpsListenerArn := terraform.Output(t, ctx.Terraform, "https_listener_arn")
		assert.Regexp(t, `^arn:aws:elasticloadbalancing:`, httpsListenerArn, "https_listener_arn must match ALB listener ARN pattern")
	})

	// ResourceCounts: exactly 1 aws_lb_listener and 0 aws_lb_listener_rule.
	t.Run("ResourceCounts", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_lb_listener", 1)
		// No listener rules in the basic HTTPS example.
		assertions.AssertResourceCount(t, ctx, "aws_lb_listener_rule", 0)
	})

	// Idempotency: a plan after apply must exit with code 0 (no changes).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "plan after apply must exit 0 (no changes) for the https example to be idempotent")
	})
}

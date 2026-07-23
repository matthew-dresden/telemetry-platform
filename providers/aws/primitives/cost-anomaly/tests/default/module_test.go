//go:build terratest

package default_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
)

// PLAN-ONLY rationale (account-singleton constraint):
//
// AWS Cost Anomaly Detection allows an account exactly ONE DIMENSIONAL (SERVICE)
// spend monitor, and a LINKED (Organizations member) account -- which the qa
// terratest account is -- can ONLY create that DIMENSIONAL type ("Linked
// accounts can only create AWS Services monitor"); CUSTOM monitors are rejected
// outright. The observability reference (providers/aws/references/observability)
// composes this exact module and applies the account's single DIMENSIONAL
// monitor + subscription in its own terratest, which provides the real-apply
// coverage of aws_ce_anomaly_monitor / aws_ce_anomaly_subscription.
//
// If THIS primitive also applied a real DIMENSIONAL monitor, the two jobs would
// race for the one account slot under the scope=all concurrent matrix and one
// would always fail with "Limit exceeded on dimensional spend monitor creation".
// Because the account cannot host a second dimensional monitor and cannot host a
// CUSTOM one at all, these tests validate the module's behaviour at PLAN time
// (the config is valid, the monitor + subscription are planned for creation, and
// the input-validation rules reject bad inputs) without applying -- so this job
// never competes for the singleton slot and is deterministic under scope=all.

// emailSubscribers constructs a self-contained EMAIL subscribers list for use as an ExtraVar.
// EMAIL subscribers are accepted directly by the Cost Anomaly Detection API and, unlike SNS
// subscribers, require no topic-access policy, keeping the example fixture self-contained.
func emailSubscribers() []map[string]interface{} {
	return []map[string]interface{}{
		{"address": "cost-anomaly-terratest@example.com", "type": "EMAIL"},
	}
}

// defaultThresholdExpression returns a valid Cost Anomaly threshold expression as JSON.
// It is self-contained test data so the suite does not depend on operator-supplied env vars.
func defaultThresholdExpression() string {
	return `{"Dimensions":{"Key":"ANOMALY_TOTAL_IMPACT_ABSOLUTE","Values":["100"],"MatchOptions":["GREATER_THAN_OR_EQUAL"]}}`
}

// TestDefaultCostAnomalyPlansMonitorAndSubscription asserts the default (DIMENSIONAL)
// example plans cleanly and that the plan creates both the anomaly monitor and its
// subscription. PLAN-ONLY: terraform plan does not create the monitor, so it neither
// hits the account's single-dimensional-monitor limit nor competes with the
// observability reference's real-apply for that slot under the scope=all matrix.
func TestDefaultCostAnomalyPlansMonitorAndSubscription(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("default-anomaly-plan-%s", suffix),
		ExtraVars: map[string]interface{}{
			"monitor_name":         fmt.Sprintf("test-monitor-%s", suffix),
			"monitor_type":         "DIMENSIONAL",
			"subscription_name":    fmt.Sprintf("test-sub-%s", suffix),
			"threshold_expression": defaultThresholdExpression(),
			"subscribers":          emailSubscribers(),
		},
	})

	planOutput, err := terraform.InitAndPlanE(t, opts)
	assert.NoError(t, err, "the default DIMENSIONAL example must plan without error")
	assert.Contains(t, planOutput, "aws_ce_anomaly_monitor.this",
		"plan must create the anomaly monitor")
	assert.Contains(t, planOutput, "aws_ce_anomaly_subscription.this",
		"plan must create the anomaly subscription")
}

// TestDefaultCostAnomalyEmptyMonitorNameValidationError asserts that an empty monitor_name causes a plan failure.
func TestDefaultCostAnomalyEmptyMonitorNameValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("default-anomaly-empty-name-%s", suffix),
		ExtraVars: map[string]interface{}{
			"monitor_name":         "",
			"subscription_name":    fmt.Sprintf("test-sub-%s", suffix),
			"threshold_expression": defaultThresholdExpression(),
			"subscribers":          emailSubscribers(),
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when monitor_name is empty")
}

// TestDefaultCostAnomalyEmptySubscribersValidationError asserts that an empty subscribers list causes a plan failure.
func TestDefaultCostAnomalyEmptySubscribersValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("default-anomaly-empty-subscribers-%s", suffix),
		ExtraVars: map[string]interface{}{
			"monitor_name":         fmt.Sprintf("test-monitor-%s", suffix),
			"subscription_name":    fmt.Sprintf("test-sub-%s", suffix),
			"threshold_expression": defaultThresholdExpression(),
			"subscribers":          []map[string]interface{}{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when subscribers list is empty (anomaly alerts must have a destination)")
}

// TestDefaultCostAnomalyMalformedThresholdExpressionError asserts that malformed threshold_expression JSON fails.
func TestDefaultCostAnomalyMalformedThresholdExpressionError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("default-anomaly-bad-threshold-%s", suffix),
		ExtraVars: map[string]interface{}{
			"monitor_name":         fmt.Sprintf("test-monitor-%s", suffix),
			"subscription_name":    fmt.Sprintf("test-sub-%s", suffix),
			"threshold_expression": "not-valid-json{{{",
			"subscribers":          emailSubscribers(),
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when threshold_expression is not valid JSON")
}

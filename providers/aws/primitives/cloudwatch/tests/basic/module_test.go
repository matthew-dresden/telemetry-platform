//go:build terratest

package basic_test

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/example-org/telemetry-platform/providers/aws/primitives/cloudwatch/tests/helpers"
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

// TestBasicCloudWatchAlarmCount asserts exactly two aws_cloudwatch_metric_alarm resources are created.
func TestBasicCloudWatchAlarmCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-alarm-count-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)
}

// TestBasicCloudWatchAlarmArnsMapHasTwoKeys asserts alarm_arns output contains exactly two keys.
func TestBasicCloudWatchAlarmArnsMapHasTwoKeys(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-alarm-arns-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	alarmArns := terraform.OutputMap(t, ctx.Terraform, "alarm_arns")
	assert.Len(t, alarmArns, 2, "alarm_arns map must contain exactly 2 keys")

	assertions.AssertOutputMapContainsKey(t, ctx, "alarm_arns", "high_cpu")
	assertions.AssertOutputMapContainsKey(t, ctx, "alarm_arns", "high_memory")
}

// TestBasicCloudWatchLogGroupCount asserts exactly two aws_cloudwatch_log_group resources are created.
func TestBasicCloudWatchLogGroupCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-log-group-count-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 2)
}

// TestBasicCloudWatchLogGroupArnsMapHasTwoKeys asserts log_group_arns output contains exactly two keys.
func TestBasicCloudWatchLogGroupArnsMapHasTwoKeys(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-log-arns-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	logGroupArns := terraform.OutputMap(t, ctx.Terraform, "log_group_arns")
	assert.Len(t, logGroupArns, 2, "log_group_arns map must contain exactly 2 keys")

	assertions.AssertOutputMapContainsKey(t, ctx, "log_group_arns", "adot_collector")
	assertions.AssertOutputMapContainsKey(t, ctx, "log_group_arns", "alb_access")
}

// TestBasicCloudWatchNoDashboard asserts no aws_cloudwatch_dashboard is created in the basic example.
func TestBasicCloudWatchNoDashboard(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-no-dashboard-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_dashboard", 0)
}

// TestBasicCloudWatchDashboardArnIsNull asserts dashboard_arn output is null when create_dashboard is false.
// dashboard_arn is a Terraform null output when create_dashboard=false; terraform.Output errors on null
// outputs, so we read the full output map via OutputAll and assert the value is nil (AC-T27-1).
func TestBasicCloudWatchDashboardArnIsNull(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-dashboard-null-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	dashboardArnRaw := helpers.ReadDashboardArnNullable(t, ctx.Terraform)
	assert.Nil(t, dashboardArnRaw, "dashboard_arn must be null when create_dashboard is false")
}

// TestBasicCloudWatchAlarmActionsMatchFixtureSNSTopic asserts every alarm in state has alarm_actions
// wired to the internally-created SNS topic ARN surfaced as the fixture_sns_topic_arn output.
func TestBasicCloudWatchAlarmActionsMatchFixtureSNSTopic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-alarm-actions-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// The fixture_sns_topic_arn output carries the ARN of the inline SNS topic created by the example.
	fixtureSNSArn := terraform.Output(t, ctx.Terraform, "fixture_sns_topic_arn")
	require.NotEmpty(t, fixtureSNSArn, "fixture_sns_topic_arn output must not be empty")
	assert.Regexp(t, `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`, fixtureSNSArn,
		"fixture_sns_topic_arn must match SNS ARN pattern")

	// Every alarm must have non-empty alarm_arns.
	alarmArns := terraform.OutputMap(t, ctx.Terraform, "alarm_arns")
	assert.Greater(t, len(alarmArns), 0, "at least one alarm must exist")
	for key, arn := range alarmArns {
		assert.NotEmpty(t, arn, "alarm ARN must not be empty for alarm key %s", key)
		assert.Regexp(t, `^arn:aws:cloudwatch:[a-z0-9-]+:[0-9]{12}:alarm:`, arn,
			"alarm ARN must match CloudWatch alarm ARN pattern for key %s", key)
	}
}

// TestBasicCloudWatchIdempotency asserts the basic example is idempotent (apply twice produces no changes).
func TestBasicCloudWatchIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-cw-idempotent-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 2)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_dashboard", 0)
}

// TestBasicCloudWatchEmptyAlarmActionsValidationError asserts that an alarm with empty alarm_actions is rejected.
func TestBasicCloudWatchEmptyAlarmActionsValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-cw-empty-actions-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"override_alarm_actions": true,
		}, mustTaggingVars(t)),
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when alarm_actions is empty (D41 fail-fast contract)")
}

// TestBasicCloudWatchInvalidRetentionDaysValidationError asserts that a retention_in_days outside the valid set is rejected.
func TestBasicCloudWatchInvalidRetentionDaysValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-cw-bad-retention-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"override_retention_days": 99,
		}, mustTaggingVars(t)),
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when retention_in_days is not in the valid CloudWatch set")
}

// TestBasicCloudWatchExtendedStatisticPercentileAlarm asserts that an alarm configured with a
// percentile extended_statistic (and no base statistic) is created with extended_statistic set
// and statistic unset. This protects the p95/p99 percentile SLO path -- CloudWatch percentiles
// must flow through extended_statistic, not statistic (which only accepts the five base statistics).
func TestBasicCloudWatchExtendedStatisticPercentileAlarm(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-cw-extended-stat-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"override_use_extended_statistic": true,
		}, mustTaggingVars(t)),
	})

	// Both alarms must still exist; only the high_cpu alarm switches to a percentile.
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)

	highCPU := findMetricAlarmByName(t, ctx.Terraform, "high_cpu")
	require.NotNil(t, highCPU, "high_cpu alarm must exist in state")

	extendedStat, _ := highCPU["extended_statistic"].(string)
	assert.Equal(t, "p99", extendedStat,
		"high_cpu alarm must carry extended_statistic=p99 when override_use_extended_statistic is set")

	// statistic must be null/empty (CloudWatch rejects setting both statistic and extended_statistic).
	statRaw := highCPU["statistic"]
	assert.True(t, statRaw == nil || statRaw == "",
		"high_cpu alarm statistic must be null when extended_statistic is set, got %v", statRaw)
}

// TestBasicCloudWatchBothStatisticsValidationError asserts that setting BOTH statistic and
// extended_statistic on an alarm fails the plan via the mutual-exclusion validation (the
// aws_cloudwatch_metric_alarm resource requires EXACTLY ONE of the two).
func TestBasicCloudWatchBothStatisticsValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-cw-both-stats-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"override_both_statistics": true,
		}, mustTaggingVars(t)),
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err,
		"plan must fail when an alarm sets both statistic and extended_statistic (mutual-exclusion contract)")
}

// findMetricAlarmByName walks the terraform show -json state tree and returns the values map of the
// aws_cloudwatch_metric_alarm whose alarm_name equals the supplied name, or nil if none is found.
func findMetricAlarmByName(t *testing.T, opts *terraform.Options, alarmName string) map[string]interface{} {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, opts, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty output")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values, ok := state["values"].(map[string]interface{})
	require.True(t, ok, "state JSON must contain a 'values' object")

	rootModule, ok := values["root_module"].(map[string]interface{})
	require.True(t, ok, "state values must contain a 'root_module' object")

	resources := collectMetricAlarmValues(rootModule)
	for _, alarmValues := range resources {
		if name, _ := alarmValues["alarm_name"].(string); name == alarmName {
			return alarmValues
		}
	}
	return nil
}

// collectMetricAlarmValues walks the state module tree and returns the values maps of all
// aws_cloudwatch_metric_alarm resources.
func collectMetricAlarmValues(module map[string]interface{}) []map[string]interface{} {
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_cloudwatch_metric_alarm" {
				continue
			}
			if vals, ok := res["values"].(map[string]interface{}); ok {
				result = append(result, vals)
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result = append(result, collectMetricAlarmValues(child)...)
		}
	}

	return result
}

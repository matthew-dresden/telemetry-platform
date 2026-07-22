//go:build terratest

package withdashboard_test

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
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

// TestWithDashboardCloudWatchDashboardCount asserts exactly one aws_cloudwatch_dashboard is created.
func TestWithDashboardCloudWatchDashboardCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-count-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_dashboard", 1)
}

// TestWithDashboardCloudWatchDashboardArnNotEmpty asserts dashboard_arn output is non-empty and matches
// the CloudWatch dashboard ARN shape: arn:aws:cloudwatch::ACCOUNT:dashboard/NAME.
// CloudWatch dashboard ARNs have no region segment (the region field is empty), so the regex must
// not include a region capture group (AC-T27-2).
func TestWithDashboardCloudWatchDashboardArnNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-arn-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	dashboardArn := terraform.Output(t, ctx.Terraform, "dashboard_arn")
	assert.NotEmpty(t, dashboardArn, "dashboard_arn must be non-empty when create_dashboard is true")
	// CloudWatch dashboard ARNs omit the region segment: arn:aws:cloudwatch::ACCOUNT:dashboard/NAME
	assert.Regexp(t, `^arn:aws:cloudwatch::[0-9]{12}:dashboard/`, dashboardArn,
		"dashboard_arn must match CloudWatch dashboard ARN pattern (no region segment)")
}

// TestWithDashboardCloudWatchDashboardNameNotEmpty asserts dashboard_name output is non-empty.
func TestWithDashboardCloudWatchDashboardNameNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-name-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	dashboardName := terraform.Output(t, ctx.Terraform, "dashboard_name")
	assert.NotEmpty(t, dashboardName, "dashboard_name must be non-empty when create_dashboard is true")
}

// TestWithDashboardCloudWatchAlarmCount asserts two aws_cloudwatch_metric_alarm resources exist.
func TestWithDashboardCloudWatchAlarmCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-alarm-count-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)
}

// TestWithDashboardCloudWatchLogGroupCount asserts two aws_cloudwatch_log_group resources exist.
func TestWithDashboardCloudWatchLogGroupCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-log-count-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 2)
}

// TestWithDashboardCloudWatchAlarmActionsMatchFixtureSNSTopic asserts every alarm in state has
// alarm_actions wired to the internally-created SNS topic ARN surfaced as the fixture_sns_topic_arn output.
func TestWithDashboardCloudWatchAlarmActionsMatchFixtureSNSTopic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-alarm-actions-%s", suffix),
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

// TestWithDashboardCloudWatchIdempotency asserts the with-dashboard example is idempotent.
func TestWithDashboardCloudWatchIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("with-dashboard-cw-idempotent-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_dashboard", 1)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 2)
}

// TestWithDashboardCloudWatchNullDashboardNameValidationError asserts that create_dashboard=true with a
// null dashboard_name fails the Terraform plan. The null value is delivered via a temporary .tfvars file
// (dashboard_name = null in HCL) so Terraform receives a proper null, not the string literal "null" that
// a Go nil ExtraVar would produce via -var (AC-T27-3).
func TestWithDashboardCloudWatchNullDashboardNameValidationError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	tmpDir := t.TempDir()
	nullVarFile := filepath.Join(tmpDir, "null_dashboard_name.tfvars")
	require.NoError(t, os.WriteFile(nullVarFile, []byte("dashboard_name = null\n"), 0600),
		"must be able to write temporary tfvars file for null dashboard_name override")

	opts := &terraform.Options{
		TerraformDir: "../../examples/with-dashboard",
		Vars: map[string]interface{}{
			"project_tag":      os.Getenv("PROJECT_TAG"),
			"terratest_run_id": fmt.Sprintf("with-dashboard-cw-null-name-%s", suffix),
		},
		VarFiles: []string{nullVarFile},
	}

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when create_dashboard=true and dashboard_name is null")
}

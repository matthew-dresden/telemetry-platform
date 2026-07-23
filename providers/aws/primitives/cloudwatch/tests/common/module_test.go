//go:build terratest

package common_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/matthew-dresden/telemetry-platform/providers/aws/primitives/cloudwatch/tests/helpers"
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

// TestCommonCloudWatchTerraformVersionBasic asserts Terraform version satisfies the pinned constraint for the basic example.
func TestCommonCloudWatchTerraformVersionBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-tf-version-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonCloudWatchTerraformVersionWithDashboard asserts Terraform version satisfies the pinned constraint for the with-dashboard example.
func TestCommonCloudWatchTerraformVersionWithDashboard(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-tf-version-dashboard-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonCloudWatchRequiredOutputsBasic asserts all required outputs are present for the basic example.
// dashboard_arn is null when create_dashboard=false; it is read via OutputAll to avoid errors on null
// outputs from terraform.Output, and asserted nil (AC-T27-1).
// fixture_sns_topic_arn and fixture_kms_key_arn are asserted non-empty to confirm inline resources were created.
func TestCommonCloudWatchRequiredOutputsBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-outputs-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	alarmArns := terraform.OutputMap(t, ctx.Terraform, "alarm_arns")
	assert.NotEmpty(t, alarmArns, "alarm_arns must not be empty")

	logGroupArns := terraform.OutputMap(t, ctx.Terraform, "log_group_arns")
	assert.NotEmpty(t, logGroupArns, "log_group_arns must not be empty")

	// dashboard_arn is null in the basic example -- read via shared helper to avoid error on null output
	dashboardArnRaw := helpers.ReadDashboardArnNullable(t, ctx.Terraform)
	assert.Nil(t, dashboardArnRaw, "dashboard_arn must be null for basic example")

	// Confirm inline SNS topic and KMS key were created by the fixture.
	fixtureSNSArn := terraform.Output(t, ctx.Terraform, "fixture_sns_topic_arn")
	require.NotEmpty(t, fixtureSNSArn, "fixture_sns_topic_arn must not be empty")
	assert.Regexp(t, `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`, fixtureSNSArn,
		"fixture_sns_topic_arn must match SNS ARN pattern")

	fixtureKMSArn := terraform.Output(t, ctx.Terraform, "fixture_kms_key_arn")
	require.NotEmpty(t, fixtureKMSArn, "fixture_kms_key_arn must not be empty")
	assert.Regexp(t, `^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/`, fixtureKMSArn,
		"fixture_kms_key_arn must match KMS key ARN pattern")
}

// TestCommonCloudWatchRequiredOutputsWithDashboard asserts all required outputs are present for the with-dashboard example.
// fixture_sns_topic_arn and fixture_kms_key_arn are asserted non-empty to confirm inline resources were created.
func TestCommonCloudWatchRequiredOutputsWithDashboard(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-outputs-dashboard-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	alarmArns := terraform.OutputMap(t, ctx.Terraform, "alarm_arns")
	assert.NotEmpty(t, alarmArns, "alarm_arns must not be empty")

	logGroupArns := terraform.OutputMap(t, ctx.Terraform, "log_group_arns")
	assert.NotEmpty(t, logGroupArns, "log_group_arns must not be empty")

	dashboardArn := terraform.Output(t, ctx.Terraform, "dashboard_arn")
	assert.NotEmpty(t, dashboardArn, "dashboard_arn must be non-empty for with-dashboard example")

	dashboardName := terraform.Output(t, ctx.Terraform, "dashboard_name")
	assert.NotEmpty(t, dashboardName, "dashboard_name must be non-empty for with-dashboard example")

	// Confirm inline SNS topic and KMS key were created by the fixture.
	fixtureSNSArn := terraform.Output(t, ctx.Terraform, "fixture_sns_topic_arn")
	require.NotEmpty(t, fixtureSNSArn, "fixture_sns_topic_arn must not be empty")
	assert.Regexp(t, `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`, fixtureSNSArn,
		"fixture_sns_topic_arn must match SNS ARN pattern")

	fixtureKMSArn := terraform.Output(t, ctx.Terraform, "fixture_kms_key_arn")
	require.NotEmpty(t, fixtureKMSArn, "fixture_kms_key_arn must not be empty")
	assert.Regexp(t, `^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/`, fixtureKMSArn,
		"fixture_kms_key_arn must match KMS key ARN pattern")
}

// TestCommonCloudWatchValidateBasic asserts the basic example passes terraform validate.
func TestCommonCloudWatchValidateBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-validate-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	alarmArns := terraform.OutputMap(t, ctx.Terraform, "alarm_arns")
	assert.NotEmpty(t, alarmArns, "alarm_arns must be non-empty after successful apply, confirming validate passed")
}

// TestCommonCloudWatchValidateWithDashboard asserts the with-dashboard example passes terraform validate.
func TestCommonCloudWatchValidateWithDashboard(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-validate-dashboard-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	dashboardArn := terraform.Output(t, ctx.Terraform, "dashboard_arn")
	assert.NotEmpty(t, dashboardArn, "dashboard_arn must be non-empty after successful apply, confirming validate passed")
}

// TestCommonCloudWatchIdempotencyBasic asserts the basic example is idempotent.
func TestCommonCloudWatchIdempotencyBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-idempotent-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 2)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_dashboard", 0)
}

// TestCommonCloudWatchIdempotencyWithDashboard asserts the with-dashboard example is idempotent.
func TestCommonCloudWatchIdempotencyWithDashboard(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	ctx := testctx.RunSingleExample(t, "../../examples", "with-dashboard", testctx.TestConfig{
		Name:      fmt.Sprintf("common-cw-idempotent-dashboard-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_dashboard", 1)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_metric_alarm", 2)
	assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_group", 2)
}

//go:build terratest

package default_test

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
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

// TestObservabilityDefault is the parent test that applies the default example once,
// runs all happy-path subtests against the live state, then destroys via t.Cleanup
// when the parent test (and all its subtests) complete.
//
// Using the parent-test pattern ensures t.Cleanup(terraform.Destroy) -- registered
// by testctx.RunSingleExample on the parent t -- fires only after ALL subtests
// complete. A previous sync.Once pattern registered cleanup on the first individual
// test's t, causing destroy to fire between test functions and leaving subsequent
// tests operating on empty state.
func TestObservabilityDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("observability-default-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	// SnsTopicArnOutputMatchesPattern asserts the sns_topic_arn output matches
	// the SNS ARN pattern (AC-3, D41).
	t.Run("SnsTopicArnOutputMatchesPattern", func(t *testing.T) {
		topicArn := terraform.Output(t, ctx.Terraform, "sns_topic_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:sns:`),
			topicArn,
			"sns_topic_arn must match ^arn:aws:sns:",
		)
	})

	// DashboardNameOutputNonEmpty asserts the dashboard_name output is non-empty (AC-3).
	t.Run("DashboardNameOutputNonEmpty", func(t *testing.T) {
		dashboardName := terraform.Output(t, ctx.Terraform, "dashboard_name")
		assert.NotEmpty(t, dashboardName, "dashboard_name output must be non-empty")
	})

	// RequiredOutputsPresent asserts all required re-exported outputs are present
	// and non-empty (AC-3).
	t.Run("RequiredOutputsPresent", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "sns_topic_arn"),
			"sns_topic_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "dashboard_name"),
			"dashboard_name must not be empty")
	})

	// AlarmActionsWiredToSnsTopicArn asserts every aws_cloudwatch_metric_alarm in
	// state has alarm_actions and ok_actions set to the composed SNS topic_arn
	// (AC-12, D41 -- no silent alert path).
	t.Run("AlarmActionsWiredToSnsTopicArn", func(t *testing.T) {
		topicArn := terraform.Output(t, ctx.Terraform, "sns_topic_arn")
		require.NotEmpty(t, topicArn, "sns_topic_arn must be non-empty to validate alarm wiring")

		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")
		require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty output")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
			"terraform show -json output must be valid JSON")

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok, "state JSON must contain a 'values' object")

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok, "state values must contain a 'root_module' object")

		alarms := findMetricAlarms(t, rootModule)
		require.NotEmpty(t, alarms, "at least one aws_cloudwatch_metric_alarm must exist in state")

		for _, alarm := range alarms {
			alarmValues, ok := alarm["values"].(map[string]interface{})
			require.True(t, ok, "alarm resource must have values")

			alarmName, _ := alarmValues["alarm_name"].(string)

			alarmActions, ok := alarmValues["alarm_actions"].([]interface{})
			require.True(t, ok, "alarm_actions must be a list for alarm %q", alarmName)
			require.NotEmpty(t, alarmActions, "alarm_actions must be non-empty for alarm %q (D41 violation)", alarmName)

			alarmActionsContainsTopic := false
			for _, action := range alarmActions {
				if actionStr, ok := action.(string); ok && actionStr == topicArn {
					alarmActionsContainsTopic = true
					break
				}
			}
			assert.True(t, alarmActionsContainsTopic,
				"alarm %q alarm_actions must contain composed SNS topic_arn %q (D41 -- no silent alert path)",
				alarmName, topicArn)

			okActions, ok := alarmValues["ok_actions"].([]interface{})
			require.True(t, ok, "ok_actions must be a list for alarm %q", alarmName)
			require.NotEmpty(t, okActions, "ok_actions must be non-empty for alarm %q (D41 violation)", alarmName)

			okActionsContainsTopic := false
			for _, action := range okActions {
				if actionStr, ok := action.(string); ok && actionStr == topicArn {
					okActionsContainsTopic = true
					break
				}
			}
			assert.True(t, okActionsContainsTopic,
				"alarm %q ok_actions must contain composed SNS topic_arn %q (D41 -- no silent alert path)",
				alarmName, topicArn)
		}
	})

	// CostAnomalySubscriptionEndpointEqualsSnsTopicArn asserts the cost-anomaly
	// subscription endpoint in state equals the composed SNS topic_arn (AC-12, D41).
	t.Run("CostAnomalySubscriptionEndpointEqualsSnsTopicArn", func(t *testing.T) {
		topicArn := terraform.Output(t, ctx.Terraform, "sns_topic_arn")
		require.NotEmpty(t, topicArn, "sns_topic_arn must be non-empty to validate cost-anomaly wiring")

		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		subscriptionEndpoints := findCostAnomalySubscriptionEndpoints(t, rootModule)
		require.NotEmpty(t, subscriptionEndpoints,
			"at least one aws_ce_anomaly_subscription must exist in state")

		for _, endpoint := range subscriptionEndpoints {
			assert.Equal(t, topicArn, endpoint,
				"cost-anomaly subscription endpoint must equal composed SNS topic_arn %q (D41)", topicArn)
		}
	})

	// SnsTopicPolicyGrantsCostAnomalyPublish asserts the composed SNS topic has an
	// access policy that (a) retains owner-account control (an aws_sns_topic_policy
	// REPLACES the default policy) and (b) grants the cost-anomaly service principal
	// costalerts.amazonaws.com SNS:Publish scoped by aws:SourceAccount, so AWS Cost
	// Anomaly Detection can publish to the CMK-encrypted topic (D41). Without this
	// statement the cost-anomaly SNS subscriber silently fails to deliver alerts.
	t.Run("SnsTopicPolicyGrantsCostAnomalyPublish", func(t *testing.T) {
		topicArn := terraform.Output(t, ctx.Terraform, "sns_topic_arn")
		require.NotEmpty(t, topicArn, "sns_topic_arn must be non-empty to validate topic policy")

		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		policies := findSnsTopicPolicies(t, rootModule)
		require.Len(t, policies, 1,
			"exactly one aws_sns_topic_policy must exist in state (the composed topic policy, D41)")

		policyStr, ok := policies[0]["policy"].(string)
		require.True(t, ok, "aws_sns_topic_policy policy must be a string")
		require.NotEmpty(t, policyStr, "aws_sns_topic_policy policy must not be empty")

		var policyDoc map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(policyStr), &policyDoc),
			"aws_sns_topic_policy policy must be valid JSON")

		stmts, ok := policyDoc["Statement"].([]interface{})
		require.True(t, ok, "topic policy must have a Statement array")

		ownerStatementFound := false
		costAnomalyStatementFound := false

		for _, s := range stmts {
			stmt, ok := s.(map[string]interface{})
			if !ok {
				continue
			}
			principal, ok := stmt["Principal"].(map[string]interface{})
			if !ok {
				continue
			}

			// Owner-account statement: Principal.AWS = arn:aws:iam::<account>:root.
			if awsPrincipal, ok := principal["AWS"].(string); ok &&
				strings.HasPrefix(awsPrincipal, "arn:aws:iam::") &&
				strings.HasSuffix(awsPrincipal, ":root") {
				ownerStatementFound = true
				assert.Equal(t, "Allow", stmt["Effect"],
					"owner-account statement must be Allow (account must retain topic control)")
				assert.Equal(t, topicArn, stmt["Resource"],
					"owner-account statement Resource must be the composed topic ARN")
			}

			// Cost-anomaly statement: Principal.Service = costalerts.amazonaws.com.
			if svc, ok := principal["Service"].(string); ok && svc == "costalerts.amazonaws.com" {
				costAnomalyStatementFound = true
				assert.Equal(t, "Allow", stmt["Effect"],
					"cost-anomaly statement must be Allow")
				assert.Equal(t, topicArn, stmt["Resource"],
					"cost-anomaly statement Resource must be the composed topic ARN")

				actions := collectStringSet(stmt["Action"])
				assert.True(t, actions["SNS:Publish"],
					"cost-anomaly statement must grant SNS:Publish; got %v", stmt["Action"])

				cond, ok := stmt["Condition"].(map[string]interface{})
				require.True(t, ok, "cost-anomaly statement must have a Condition")
				strEquals, ok := cond["StringEquals"].(map[string]interface{})
				require.True(t, ok, "cost-anomaly statement Condition must use StringEquals")
				srcAccount, ok := strEquals["aws:SourceAccount"].(string)
				require.True(t, ok, "cost-anomaly statement must scope by aws:SourceAccount")
				assert.Regexp(t, regexp.MustCompile(`^[0-9]{12}$`), srcAccount,
					"aws:SourceAccount must be the 12-digit owner account id")
			}
		}

		assert.True(t, ownerStatementFound,
			"topic policy must retain an owner-account (root) statement -- a topic policy REPLACES the default, so omitting it removes account control")
		assert.True(t, costAnomalyStatementFound,
			"topic policy must grant costalerts.amazonaws.com SNS:Publish so Cost Anomaly Detection can publish to the CMK-encrypted topic (D41)")
	})

	// SnsTopicPolicyResourceCount asserts exactly one aws_sns_topic_policy is created
	// (the composed default policy is applied for every env that uses this reference, D41).
	t.Run("SnsTopicPolicyResourceCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_sns_topic_policy", 1)
	})

	// BudgetDirectEmailSubscribersPreserved asserts the budget in state retains its
	// direct subscriber_email_addresses and does not route to the SNS topic
	// (AC-12, docs/terragrunt-concepts.md).
	t.Run("BudgetDirectEmailSubscribersPreserved", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		budgets := findBudgets(t, rootModule)
		require.NotEmpty(t, budgets, "at least one aws_budgets_budget must exist in state")

		for _, budget := range budgets {
			budgetValues, ok := budget["values"].(map[string]interface{})
			require.True(t, ok, "budget resource must have values")

			budgetName, _ := budgetValues["name"].(string)

			notifications, ok := budgetValues["notification"].([]interface{})
			require.True(t, ok, "budget %q must have notification blocks", budgetName)
			require.NotEmpty(t, notifications, "budget %q must have at least one notification", budgetName)

			for _, rawNotification := range notifications {
				notification, ok := rawNotification.(map[string]interface{})
				if !ok {
					continue
				}

				subscriberEmails, _ := notification["subscriber_email_addresses"].([]interface{})
				assert.NotEmpty(t, subscriberEmails,
					"budget %q notification must have direct subscriber_email_addresses (D41: budget keeps direct email subscribers)",
					budgetName)
			}
		}
	})

	// BudgetNotificationThresholdsMatch asserts the provisioned budget's notification
	// thresholds in state equal the supplied percentage increments of budget_amount
	// (AC-12, docs/terragrunt-concepts.md).
	t.Run("BudgetNotificationThresholdsMatch", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		budgets := findBudgets(t, rootModule)
		require.NotEmpty(t, budgets, "at least one aws_budgets_budget must exist in state")

		for _, budget := range budgets {
			budgetValues, ok := budget["values"].(map[string]interface{})
			require.True(t, ok, "budget resource must have values")

			budgetName, _ := budgetValues["name"].(string)

			notifications, ok := budgetValues["notification"].([]interface{})
			require.True(t, ok, "budget %q must have notification blocks", budgetName)
			require.NotEmpty(t, notifications,
				"budget %q must have at least one notification threshold", budgetName)

			for _, rawNotification := range notifications {
				notification, ok := rawNotification.(map[string]interface{})
				if !ok {
					continue
				}

				threshold, _ := notification["threshold"].(float64)
				thresholdType, _ := notification["threshold_type"].(string)
				assert.Greater(t, threshold, 0.0,
					"budget %q notification threshold must be > 0", budgetName)
				assert.Equal(t, "PERCENTAGE", thresholdType,
					"budget %q notification threshold_type must be PERCENTAGE", budgetName)
			}
		}
	})

	// SnsTopicResourceCount asserts exactly one aws_sns_topic is created (AC-3).
	t.Run("SnsTopicResourceCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_sns_topic", 1)
	})

	// CloudwatchAlarmResourceCount asserts at least one aws_cloudwatch_metric_alarm
	// is created (AC-3).
	t.Run("CloudwatchAlarmResourceCount", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		alarms := findMetricAlarms(t, rootModule)
		assert.GreaterOrEqual(t, len(alarms), 1,
			"at least one aws_cloudwatch_metric_alarm must exist in state")
	})

	// CostAnomalySubscriptionResourceCount asserts exactly one
	// aws_ce_anomaly_subscription is created (AC-3, D23).
	t.Run("CostAnomalySubscriptionResourceCount", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_ce_anomaly_subscription", 1)
	})

	// PercentileAlarmUsesExtendedStatistic asserts the percentile (p95) alarm flows through
	// the reference -> cloudwatch primitive with extended_statistic set and statistic unset.
	// CloudWatch rejects percentile values supplied via statistic, so the p95 SLO alarm must
	// carry extended_statistic; setting both would also be rejected (AC-4, percentile SLO path).
	t.Run("PercentileAlarmUsesExtendedStatistic", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		alarms := findMetricAlarms(t, rootModule)
		require.NotEmpty(t, alarms, "at least one aws_cloudwatch_metric_alarm must exist in state")

		var percentileAlarm map[string]interface{}
		for _, alarm := range alarms {
			alarmValues, ok := alarm["values"].(map[string]interface{})
			require.True(t, ok, "alarm resource must have values")
			if extStat, _ := alarmValues["extended_statistic"].(string); extStat == "p95" {
				percentileAlarm = alarmValues
				break
			}
		}
		require.NotNil(t, percentileAlarm,
			"a percentile alarm with extended_statistic=p95 must exist in state (percentile SLO path)")

		statRaw := percentileAlarm["statistic"]
		assert.True(t, statRaw == nil || statRaw == "",
			"percentile alarm statistic must be null when extended_statistic is set, got %v", statRaw)
	})

	// SnsTopicEmailSubscriptionComposed asserts the module composes an SNS email subscription
	// to the configured alarm_notification_email so CloudWatch alarms + cost-anomaly alerts have
	// a human delivery path (OBS-3 -- the topic previously had zero subscribers and every alarm
	// fired into a void). The subscription is created in PendingConfirmation state; this asserts
	// the resource + endpoint are composed. Confirmation is an out-of-band operator action (the
	// recipient clicks the link AWS emails) and is intentionally NOT asserted here.
	t.Run("SnsTopicEmailSubscriptionComposed", func(t *testing.T) {
		wantEmail := terraform.Output(t, ctx.Terraform, "alarm_notification_email")
		require.NotEmpty(t, wantEmail, "alarm_notification_email output must be non-empty to validate the subscription")

		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		subs := findSnsTopicSubscriptions(t, rootModule)
		require.NotEmpty(t, subs,
			"at least one aws_sns_topic_subscription must exist -- a subscriber-less alarm topic is the OBS-3 silent-delivery gap")

		emailMatch := false
		for _, s := range subs {
			proto, _ := s["protocol"].(string)
			endpoint, _ := s["endpoint"].(string)
			if proto == "email" && endpoint == wantEmail {
				emailMatch = true
				break
			}
		}
		assert.True(t, emailMatch,
			"an email-protocol SNS subscription to %q must be composed from alarm_notification_email (OBS-3 human delivery path)", wantEmail)
	})

	// AlarmTreatMissingDataFlowsThrough asserts per-alarm treat_missing_data round-trips through
	// the reference -> cloudwatch primitive into state (OBS-4a). The example sets the task-down
	// alarm to "breaching" (a stopped service stops emitting RunningTaskCount, so absent data MUST
	// alarm rather than sit in INSUFFICIENT_DATA) and the error-rate alarm to "notBreaching"
	// (missing == healthy). A regression that dropped the per-alarm value would silently revert
	// task-down to the "missing" default and never fire on a real outage.
	t.Run("AlarmTreatMissingDataFlowsThrough", func(t *testing.T) {
		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state))

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok)

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok)

		alarms := findMetricAlarms(t, rootModule)
		require.NotEmpty(t, alarms, "at least one aws_cloudwatch_metric_alarm must exist in state")

		treatMissingByName := map[string]string{}
		for _, alarm := range alarms {
			alarmValues, ok := alarm["values"].(map[string]interface{})
			require.True(t, ok, "alarm resource must have values")
			name, _ := alarmValues["alarm_name"].(string)
			tmd, _ := alarmValues["treat_missing_data"].(string)
			treatMissingByName[name] = tmd
		}

		// The example run-id-scopes its alarm names as "<base>-<run_suffix>" so concurrent
		// runs in the shared qa account never collide on a fixed CloudWatch alarm name (#161).
		// Look each target alarm up by its STABLE base name (verbatim or with the per-run
		// suffix) rather than a hardcoded literal the suffix would never match -- this keeps
		// the OBS-4a assertion robust to the run-id scoping without weakening its intent.
		taskDownTMD, taskDownName, taskDownCount := treatMissingForBase(treatMissingByName, "task-down")
		require.Equal(t, 1, taskDownCount,
			"exactly one breaching task-down alarm must exist in state (base name 'task-down', run-id-scoped per #161); got %d", taskDownCount)
		assert.Equal(t, "breaching", taskDownTMD,
			"task-down alarm %q treat_missing_data must be 'breaching' so a stopped service alarms with no datapoints (OBS-4a)", taskDownName)

		errorRateTMD, errorRateName, errorRateCount := treatMissingForBase(treatMissingByName, "high-error-rate")
		require.Equal(t, 1, errorRateCount,
			"exactly one high-error-rate alarm must exist in state (base name 'high-error-rate', run-id-scoped per #161); got %d", errorRateCount)
		assert.Equal(t, "notBreaching", errorRateTMD,
			"high-error-rate alarm %q treat_missing_data must be 'notBreaching' (missing == healthy)", errorRateName)
	})

	// TerraformVersion asserts the pinned Terraform version is satisfied (AC-1).
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency asserts a plan after apply exits with code 0 (AC-1).
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (idempotency requirement)")
	})

	// AlarmActionNotWiredToTopicIsDetectable verifies the test logic can detect when
	// an alarm action is NOT wired to the SNS topic_arn (D41 guard).
	// Confirms the sns_topic_arn is an actual SNS ARN so the alarm_actions wiring
	// check is meaningful.
	t.Run("AlarmActionNotWiredToTopicIsDetectable", func(t *testing.T) {
		topicArn := terraform.Output(t, ctx.Terraform, "sns_topic_arn")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`),
			topicArn,
			"sns_topic_arn must be an SNS topic ARN -- non-SNS action targets reintroduce the D41 silent-alert blocker",
		)
	})
}

// ---------------------------------------------------------------------------
// State-walking helpers
// ---------------------------------------------------------------------------

// findMetricAlarms walks the Terraform state module tree and returns all
// aws_cloudwatch_metric_alarm resource values maps.
func findMetricAlarms(t *testing.T, module map[string]interface{}) []map[string]interface{} {
	t.Helper()
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType == "aws_cloudwatch_metric_alarm" {
				result = append(result, res)
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result = append(result, findMetricAlarms(t, child)...)
		}
	}

	return result
}

// findBudgets walks the Terraform state module tree and returns all
// aws_budgets_budget resource value maps.
func findBudgets(t *testing.T, module map[string]interface{}) []map[string]interface{} {
	t.Helper()
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType == "aws_budgets_budget" {
				result = append(result, res)
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result = append(result, findBudgets(t, child)...)
		}
	}

	return result
}

// findSnsTopicPolicies walks the Terraform state module tree and returns the values
// maps of all aws_sns_topic_policy resources.
func findSnsTopicPolicies(t *testing.T, module map[string]interface{}) []map[string]interface{} {
	t.Helper()
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_sns_topic_policy" {
				continue
			}
			if resValues, ok := res["values"].(map[string]interface{}); ok {
				result = append(result, resValues)
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result = append(result, findSnsTopicPolicies(t, child)...)
		}
	}

	return result
}

// findSnsTopicSubscriptions walks the Terraform state module tree and returns the values
// maps of all aws_sns_topic_subscription resources.
func findSnsTopicSubscriptions(t *testing.T, module map[string]interface{}) []map[string]interface{} {
	t.Helper()
	var result []map[string]interface{}

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_sns_topic_subscription" {
				continue
			}
			if resValues, ok := res["values"].(map[string]interface{}); ok {
				result = append(result, resValues)
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			result = append(result, findSnsTopicSubscriptions(t, child)...)
		}
	}

	return result
}

// treatMissingForBase resolves an alarm by its STABLE base name in a name->treat_missing_data
// map. The example run-id-scopes alarm names as "<base>-<run_suffix>" for concurrent-run
// isolation (#161), so a match is the name verbatim OR prefixed with "<base>-". It returns the
// matched alarm's treat_missing_data value, its actual (possibly suffixed) name, and the number
// of matches, so the caller can require exactly one. Matching the stable base keeps the OBS-4a
// assertion robust to the per-run suffix instead of a hardcoded literal the suffix never matches.
func treatMissingForBase(byName map[string]string, base string) (string, string, int) {
	var (
		matchedValue string
		matchedName  string
		count        int
	)
	for name, tmd := range byName {
		if name == base || strings.HasPrefix(name, base+"-") {
			matchedValue = tmd
			matchedName = name
			count++
		}
	}
	return matchedValue, matchedName, count
}

// collectStringSet normalises an IAM policy Action field (which may be a single string
// or a list of strings) into a set for membership checks.
func collectStringSet(raw interface{}) map[string]bool {
	out := map[string]bool{}
	switch v := raw.(type) {
	case string:
		out[v] = true
	case []interface{}:
		for _, item := range v {
			if s, ok := item.(string); ok {
				out[s] = true
			}
		}
	}
	return out
}

// findCostAnomalySubscriptionEndpoints walks the state tree and returns all
// SNS-type subscriber addresses from aws_ce_anomaly_subscription resources.
func findCostAnomalySubscriptionEndpoints(t *testing.T, module map[string]interface{}) []string {
	t.Helper()
	var endpoints []string

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_ce_anomaly_subscription" {
				continue
			}
			resValues, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			subscribers, ok := resValues["subscriber"].([]interface{})
			if !ok {
				continue
			}
			for _, rawSub := range subscribers {
				sub, ok := rawSub.(map[string]interface{})
				if !ok {
					continue
				}
				subType, _ := sub["type"].(string)
				if subType == "SNS" {
					if addr, ok := sub["address"].(string); ok {
						endpoints = append(endpoints, addr)
					}
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			endpoints = append(endpoints, findCostAnomalySubscriptionEndpoints(t, child)...)
		}
	}

	return endpoints
}

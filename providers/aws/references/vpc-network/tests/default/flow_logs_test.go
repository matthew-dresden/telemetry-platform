//go:build terratest

package default_test

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestVpcNetworkFlowLogsResourcesInPlan verifies that the examples/default fixture
// plans all resources required to enable VPC Flow Logs (satisfying trivy AWS-0178):
//   - aws_cloudwatch_log_group for flow log destination
//   - aws_iam_role with vpc-flow-logs.amazonaws.com assume-role trust
//   - aws_iam_role_policy granting CloudWatch Logs write permissions
//   - aws_kms_key for log group encryption
//
// This is a plan-only test: it runs terraform init + plan without applying
// so that no real AWS resources are created.
func TestVpcNetworkFlowLogsResourcesInPlan(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	opts := &terraform.Options{
		TerraformDir: "../../examples/default",
		Vars: map[string]interface{}{
			"name": fmt.Sprintf("vpc-flow-logs-plan-%s", suffix),
		},
		NoColor: true,
	}

	terraform.Init(t, opts)
	planOutput, err := terraform.PlanE(t, opts)
	require.NoError(t, err, "terraform plan must exit zero for examples/default")

	assert.True(t,
		strings.Contains(planOutput, "aws_cloudwatch_log_group.vpc_flow_log"),
		"plan must include aws_cloudwatch_log_group.vpc_flow_log; got plan:\n%s", planOutput,
	)

	assert.True(t,
		strings.Contains(planOutput, "aws_iam_role.vpc_flow_log"),
		"plan must include aws_iam_role.vpc_flow_log; got plan:\n%s", planOutput,
	)

	assert.True(t,
		strings.Contains(planOutput, "aws_iam_role_policy.vpc_flow_log"),
		"plan must include aws_iam_role_policy.vpc_flow_log; got plan:\n%s", planOutput,
	)

	assert.True(t,
		strings.Contains(planOutput, "aws_kms_key.flow_log"),
		"plan must include aws_kms_key.flow_log for log group encryption; got plan:\n%s", planOutput,
	)

	assert.True(t,
		strings.Contains(planOutput, "vpc-flow-logs.amazonaws.com"),
		"plan must reference vpc-flow-logs.amazonaws.com in IAM trust policy; got plan:\n%s", planOutput,
	)
}

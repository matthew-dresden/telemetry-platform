//go:build terratest

package common_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
)

// This package holds the plan-only, input-driven validation-error cases for the
// alb-listener module. Each case needs a DIFFERENT (invalid) input, so it runs
// `terraform plan` only -- no apply, no real infrastructure, no listener
// provisioning. The apply-level coverage (version, outputs, attributes,
// resource counts, idempotency) lives in the per-example packages
// (tests/https, tests/http-redirect), where each example is applied exactly
// once and every assertion runs as a subtest against that single applied state.

// TestCommonALBListenerInvalidLoadBalancerARNError tests that an invalid load_balancer_arn fails validation.
func TestCommonALBListenerInvalidLoadBalancerARNError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/https", testctx.TestConfig{
		Name: fmt.Sprintf("common-albl-bad-lb-arn-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":                       fmt.Sprintf("albl-ml-%s", suffix[:8]),
			"load_balancer_arn_override": "not-an-arn",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when load_balancer_arn does not match ARN regex")
}

// TestCommonALBListenerMissingSslPolicyError tests that an HTTPS listener without ssl_policy fails validation.
func TestCommonALBListenerMissingSslPolicyError(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/https", testctx.TestConfig{
		Name: fmt.Sprintf("common-albl-no-ssl-%s", suffix),
		ExtraVars: map[string]interface{}{
			"name":       fmt.Sprintf("albl-ns-%s", suffix[:8]),
			"ssl_policy": "",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when HTTPS listener has empty ssl_policy")
}

//go:build terratest

package common_test

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

// mustTaggingVarsUnique extends mustTaggingVars with a per-test-unique suffix on the
// terratest_run_id. Use this for tests that apply the interface example so that each
// test invocation generates distinct resource names (CW log group, IAM role, KMS key).
// Multiple common tests in the same package share the same TERRATEST_RUN_ID env var;
// appending a timestamp suffix prevents ResourceAlreadyExistsException when
// CloudWatch log group deletion has not yet propagated between sequential tests.
// Only the last 6 digits of suffix are used to keep IAM role names under 64 chars.
func mustTaggingVarsUnique(t *testing.T, suffix string) map[string]interface{} {
	t.Helper()
	runID := os.Getenv("TERRATEST_RUN_ID")
	if runID == "" {
		t.Fatal("ERROR: TERRATEST_RUN_ID is not set; run via make tf-test")
	}
	projectTag := os.Getenv("PROJECT_TAG")
	if projectTag == "" {
		t.Fatal("ERROR: PROJECT_TAG is not set; run via make tf-test")
	}
	shortSuffix := suffix
	if len(shortSuffix) > 6 {
		shortSuffix = shortSuffix[len(shortSuffix)-6:]
	}
	return map[string]interface{}{
		"project_tag":      projectTag,
		"terratest_run_id": fmt.Sprintf("%s-%s", runID, shortSuffix),
	}
}

// TestCommonValidatePasses asserts the interface example passes terraform validate (confirmed by successful apply).
func TestCommonValidatePasses(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "interface", testctx.TestConfig{
		Name:      fmt.Sprintf("common-validate-iface-%s", suffix),
		ExtraVars: mustTaggingVarsUnique(t, suffix),
	})

	endpointIDs := terraform.OutputMap(t, ctx.Terraform, "endpoint_ids")
	assert.NotEmpty(t, endpointIDs, "endpoint_ids must be non-empty, confirming validate passed")
}

// TestCommonFmtCheck asserts the module source files are formatted (via successful apply confirming no format drift).
func TestCommonFmtCheck(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "gateway", testctx.TestConfig{
		Name:      fmt.Sprintf("common-fmt-gw-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	endpointIDs := terraform.OutputMap(t, ctx.Terraform, "endpoint_ids")
	assert.NotEmpty(t, endpointIDs, "endpoint_ids must be non-empty, confirming fmt check passed")
}

// TestCommonRequiredOutputsInterface asserts the interface example exposes endpoint_ids and endpoint_dns_entries.
func TestCommonRequiredOutputsInterface(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "interface", testctx.TestConfig{
		Name:      fmt.Sprintf("common-outputs-iface-%s", suffix),
		ExtraVars: mustTaggingVarsUnique(t, suffix),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "endpoint_ids"), "endpoint_ids must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "endpoint_dns_entries"), "endpoint_dns_entries must not be empty")
}

// TestCommonRequiredOutputsGateway asserts the gateway example exposes endpoint_ids.
func TestCommonRequiredOutputsGateway(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "gateway", testctx.TestConfig{
		Name:      fmt.Sprintf("common-outputs-gw-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "endpoint_ids"), "endpoint_ids must not be empty")
}

// TestCommonTerraformVersionInterface asserts the pinned Terraform version constraint on the interface example.
func TestCommonTerraformVersionInterface(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "interface", testctx.TestConfig{
		Name:      fmt.Sprintf("common-ver-iface-%s", suffix),
		ExtraVars: mustTaggingVarsUnique(t, suffix),
	})
	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonTerraformVersionGateway asserts the pinned Terraform version constraint on the gateway example.
func TestCommonTerraformVersionGateway(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "gateway", testctx.TestConfig{
		Name:      fmt.Sprintf("common-ver-gw-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})
	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonEmptyEndpointsList asserts that an empty endpoints list causes a plan error.
func TestCommonEmptyEndpointsList(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/interface", testctx.TestConfig{
		Name: fmt.Sprintf("common-empty-list-%s", suffix),
		ExtraVars: map[string]interface{}{
			"endpoints": []map[string]interface{}{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when endpoints list is empty")
}

// TestCommonInvalidEndpointType asserts that a vpc_endpoint_type outside allowed values causes a plan error.
func TestCommonInvalidEndpointType(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/interface", testctx.TestConfig{
		Name: fmt.Sprintf("common-invalid-type-%s", suffix),
		ExtraVars: map[string]interface{}{
			"endpoints": []map[string]interface{}{
				{
					"name":              "ssm",
					"service_name":      "com.amazonaws.us-east-1.ssm",
					"vpc_endpoint_type": "Invalid",
					"subnet_ids":        []string{"subnet-12345678"},
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when vpc_endpoint_type is not Interface, Gateway, or GatewayLoadBalancer")
}

// TestCommonInterfaceRequiresSubnetIDs asserts that an Interface endpoint with empty subnet_ids causes a plan error.
func TestCommonInterfaceRequiresSubnetIDs(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/interface", testctx.TestConfig{
		Name: fmt.Sprintf("common-iface-no-subnets-%s", suffix),
		ExtraVars: map[string]interface{}{
			"endpoints": []map[string]interface{}{
				{
					"name":              "ssm",
					"service_name":      "com.amazonaws.us-east-1.ssm",
					"vpc_endpoint_type": "Interface",
					"subnet_ids":        []string{},
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when Interface endpoint has empty subnet_ids")
}

// TestCommonGatewayRequiresRouteTableIDs asserts that a Gateway endpoint with empty route_table_ids causes a plan error.
func TestCommonGatewayRequiresRouteTableIDs(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/gateway", testctx.TestConfig{
		Name: fmt.Sprintf("common-gw-no-rt-%s", suffix),
		ExtraVars: map[string]interface{}{
			"endpoints": []map[string]interface{}{
				{
					"name":              "s3",
					"service_name":      "com.amazonaws.us-east-1.s3",
					"vpc_endpoint_type": "Gateway",
					"route_table_ids":   []string{},
				},
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when Gateway endpoint has empty route_table_ids")
}

// TestCommonInvalidVpcID asserts that a vpc_id not matching ^vpc- causes a plan error.
func TestCommonInvalidVpcID(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/interface", testctx.TestConfig{
		Name: fmt.Sprintf("common-invalid-vpc-%s", suffix),
		ExtraVars: map[string]interface{}{
			"vpc_id": "invalid-vpc-id",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when vpc_id does not match ^vpc-")
}

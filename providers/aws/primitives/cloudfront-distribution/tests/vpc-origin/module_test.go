//go:build terratest

package vpc_origin_test

import (
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// vpcOriginNameMaxLength is CloudFront's maximum VPC origin Name length. CreateVpcOrigin
// rejects a longer Name with "InvalidArgument: The parameter VPC Origin Name is too big.".
// AWS does not publish the limit, so it is asserted here; verified empirically against the
// live CloudFront API (us-east-1, 2026-06-28): a 64-character Name is accepted and a
// 65-character Name is rejected. This mirrors local.vpc_origin_name_max_length in the module.
const vpcOriginNameMaxLength = 64

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

// vpcOriginApplyPrereqs gates the apply-based test. Creating the distribution requires a
// real publicly-trusted (DNS-validated) us-east-1 ACM certificate that covers the alias
// (CloudFront rejects self-signed/imported certs and reserved alias domains), AND a real,
// Active internal ALB ARN (CreateVpcOrigin validates the ARN refers to a deployed
// resource). Both are environment-level prerequisites a self-contained example cannot
// provision, so the apply test skips with an actionable message when they are unset. The
// negative tests below exercise the VPC-origin HCL path without these prerequisites, and
// module-validate runs `terraform validate` on the example offline.
func vpcOriginApplyPrereqs(t *testing.T) (certArn, alias, albArn string) {
	t.Helper()
	certArn = os.Getenv("TT_CLOUDFRONT_CERT_ARN")
	alias = os.Getenv("TT_CLOUDFRONT_ALIAS")
	albArn = os.Getenv("TT_VPC_ORIGIN_ALB_ARN")
	if certArn == "" || alias == "" || albArn == "" {
		t.Skipf("SKIP: requires a real us-east-1 DNS-validated ACM cert (TT_CLOUDFRONT_CERT_ARN), " +
			"a matching owned alias (TT_CLOUDFRONT_ALIAS), and a real Active internal ALB ARN " +
			"(TT_VPC_ORIGIN_ALB_ARN). CreateVpcOrigin validates the ALB ARN refers to a deployed " +
			"resource, so a placeholder ARN cannot be applied; the real end-to-end VPC-origin apply " +
			"path is exercised by the collector-ingestion reference module.")
	}
	return certArn, alias, albArn
}

// TestVpcOriginCreatesVpcOriginResource asserts that an origin_type = "vpc" configuration
// provisions exactly one aws_cloudfront_vpc_origin and one aws_cloudfront_distribution, and
// exposes a non-empty vpc_origin_id -- proving the distribution reaches the internal ALB via
// the VPC origin (no public custom origin, no self-loop). Gated on the apply prerequisites.
func TestVpcOriginCreatesVpcOriginResource(t *testing.T) {
	certArn, alias, albArn := vpcOriginApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "vpc-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-vpc-origin-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"acm_certificate_arn": certArn,
			"aliases":             []string{alias},
			"vpc_origin_alb_arn":  albArn,
			"vpc_origin_domain":   alias,
			"log_bucket_suffix":   suffix,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_vpc_origin", 1)
	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_distribution", 1)
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "vpc_origin_id"), "vpc_origin_id must not be empty for a vpc origin")
}

// TestVpcOriginLongNameCreatesSuccessfully is the regression guard for the VPC origin
// Name-length bound. It drives the module with a REPRESENTATIVE LONG, per-run-unique
// origin_id (>= the longest real collector namespace) so the natural
// "<origin_id>-vpc-origin" name overflows CloudFront's 64-character limit and the module
// must deterministically bound it. The vpc-origin-long-name fixture stands up the real
// prerequisites CreateVpcOrigin validates (a real Active internal ALB whose VPC has an
// internet gateway) plus a publicly-trusted DNS-validated ACM viewer certificate, so this
// is a REAL apply: it proves the bounded long name is ACCEPTED by CreateVpcOrigin (the
// distribution + VPC origin are created) and that the resulting Name is <= the limit.
//
// This is the gap that let the qa terratest pass while a prod apply failed: the qa
// namespace was short enough that "<origin_id>-vpc-origin" fit in 64 characters, so the
// boundary was never exercised by a real apply until prod.
//
// The fixture needs the qa sandbox domain + its live parent zone id (so ACM DNS validation
// reaches ISSUED); the CI tf-test job injects both as TF_VAR_sandbox_domain and
// TF_VAR_sandbox_parent_zone_id. The test skips with an actionable message when they are
// unset (e.g. a local run without the qa sandbox domain).
func TestVpcOriginLongNameCreatesSuccessfully(t *testing.T) {
	if os.Getenv("TF_VAR_sandbox_domain") == "" || os.Getenv("TF_VAR_sandbox_parent_zone_id") == "" {
		t.Skip("SKIP: requires TF_VAR_sandbox_domain and TF_VAR_sandbox_parent_zone_id (the qa " +
			"sandbox domain and its live, NS-delegated parent zone id) so the fixture can DNS-validate " +
			"a real publicly-trusted ACM viewer certificate. The CI tf-test job injects both; CloudFront " +
			"rejects self-signed/imported certs, so this apply cannot run without them.")
	}

	suffix := fmt.Sprintf("%d", time.Now().Unix())

	// Per-run-unique origin_id modelled on the collector sandbox namespace and long enough
	// that "<origin_id>-vpc-origin" exceeds the limit, so the module's truncation branch runs.
	originID := fmt.Sprintf("telemetry-useast1-sandbox-%s-collector-ingestion-adot", suffix)
	require.Greater(t, len(originID)+len("-vpc-origin"), vpcOriginNameMaxLength,
		"test setup error: origin_id must be long enough that the unbounded VPC origin name exceeds the limit")

	ctx := testctx.RunSingleExample(t, "../../examples", "vpc-origin-long-name", testctx.TestConfig{
		Name: fmt.Sprintf("cf-vpc-longname-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"origin_id": originID,
			"suffix":    suffix,
		}, mustTaggingVars(t)),
	})

	// CreateVpcOrigin and CreateDistribution both succeeded with the bounded long name.
	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_vpc_origin", 1)
	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_distribution", 1)
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "vpc_origin_id"),
		"vpc_origin_id must be non-empty -- CreateVpcOrigin must have succeeded with the bounded long name")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_id"),
		"distribution_id must be non-empty -- the distribution must have been created")

	// The applied VPC origin Name must be within CloudFront's limit.
	boundedName := terraform.Output(t, ctx.Terraform, "vpc_origin_name")
	assert.NotEmpty(t, boundedName, "vpc_origin_name must be non-empty for a vpc origin")
	assert.LessOrEqual(t, len(boundedName), vpcOriginNameMaxLength,
		"the bounded VPC origin Name %q (%d chars) must be <= CloudFront's %d-char limit",
		boundedName, len(boundedName), vpcOriginNameMaxLength)

	// Prove the long-name branch was actually exercised: the UNBOUNDED name exceeded the limit.
	desiredLen, err := strconv.Atoi(terraform.Output(t, ctx.Terraform, "vpc_origin_desired_name_length"))
	require.NoError(t, err, "vpc_origin_desired_name_length output must be an integer")
	assert.Greater(t, desiredLen, vpcOriginNameMaxLength,
		"the unbounded '<origin_id>-vpc-origin' name (%d chars) must exceed the limit so the bound is genuinely exercised",
		desiredLen)
}

// TestVpcOriginMissingArnRejected asserts the plan fails when origin_type = "vpc" but
// vpc_origin_alb_arn is empty -- the module's origin.vpc_origin_arn validation must fire.
// This is the regression guard for the VPC-origin wiring and runs without an apply.
func TestVpcOriginMissingArnRejected(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/vpc-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-vpc-noarn-%s", suffix),
		ExtraVars: map[string]interface{}{
			"acm_certificate_arn": fmt.Sprintf("arn:aws:acm:us-east-1:123456789012:certificate/%s", suffix),
			"web_acl_id":          "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test/00000000-0000-0000-0000-000000000001",
			"aliases":             []string{fmt.Sprintf("collector-%s.example.com", suffix)},
			"vpc_origin_alb_arn":  "",
			"vpc_origin_domain":   fmt.Sprintf("collector-%s.example.com", suffix),
			"log_bucket_suffix":   suffix,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when origin_type is vpc but vpc_origin_alb_arn is empty")
}

// TestVpcOriginSubTLSVersionRejected asserts plan fails when minimum_protocol_version is below
// TLSv1.2_2021 for a vpc origin (the DoD-required transport-security floor applies to every origin type).
func TestVpcOriginSubTLSVersionRejected(t *testing.T) {
	rejectedVersions := []string{"TLSv1.1_2016", "TLSv1_2016", "TLSv1.2_2018", "TLSv1.2_2019"}
	for _, version := range rejectedVersions {
		version := version
		t.Run(version, func(t *testing.T) {
			suffix := fmt.Sprintf("%d", time.Now().Unix())
			opts := testctx.InitTerraform("../../examples/vpc-origin", testctx.TestConfig{
				Name: fmt.Sprintf("cf-vpc-tls-fail-%s-%s", version, suffix),
				ExtraVars: map[string]interface{}{
					"acm_certificate_arn":      fmt.Sprintf("arn:aws:acm:us-east-1:123456789012:certificate/%s", suffix),
					"web_acl_id":               "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test/00000000-0000-0000-0000-000000000001",
					"aliases":                  []string{fmt.Sprintf("collector-%s.example.com", suffix)},
					"minimum_protocol_version": version,
					"vpc_origin_domain":        fmt.Sprintf("collector-%s.example.com", suffix),
					"log_bucket_suffix":        suffix,
				},
			})

			_, err := terraform.InitAndPlanE(t, opts)
			assert.Error(t, err, "plan must fail when minimum_protocol_version is %s (below TLSv1.2_2021)", version)
		})
	}
}

// TestVpcOriginEmptyCertARNRejected asserts plan fails when acm_certificate_arn is empty and aliases are set.
func TestVpcOriginEmptyCertARNRejected(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/vpc-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-vpc-empty-cert-%s", suffix),
		ExtraVars: map[string]interface{}{
			"acm_certificate_arn": "",
			"web_acl_id":          "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test/00000000-0000-0000-0000-000000000001",
			"aliases":             []string{fmt.Sprintf("collector-%s.example.com", suffix)},
			"vpc_origin_domain":   fmt.Sprintf("collector-%s.example.com", suffix),
			"log_bucket_suffix":   suffix,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when acm_certificate_arn is empty")
}

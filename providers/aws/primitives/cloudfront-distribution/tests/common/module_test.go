//go:build terratest

package common_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
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


// cloudFrontApplyPrereqs gates the apply-based tests. Creating a CloudFront distribution
// requires a viewer certificate that is (a) issued by a publicly-trusted CA and (b) covers
// the alias (CNAME); CloudFront rejects self-signed/imported certs and reserved alias
// domains. A trusted, DNS-validated certificate plus an owned alias domain is an
// environment-level prerequisite that cannot be provisioned from a self-contained terratest.
// The apply tests require TT_CLOUDFRONT_CERT_ARN (a real us-east-1 validated cert ARN) and
// TT_CLOUDFRONT_ALIAS (a matching owned alias) and skip with an actionable message otherwise.
// The CLOUDFRONT-scope WAF Web ACL is still self-provisioned by the example.
func cloudFrontApplyPrereqs(t *testing.T) (certArn string, alias string) {
	t.Helper()
	certArn = os.Getenv("TT_CLOUDFRONT_CERT_ARN")
	alias = os.Getenv("TT_CLOUDFRONT_ALIAS")
	if certArn == "" || alias == "" {
		t.Skipf("SKIP: requires a real publicly-trusted (DNS-validated) ACM certificate in " +
			"us-east-1 and an owned alias domain it covers. CloudFront rejects self-signed/imported " +
			"certs and reserved alias domains (example.com) at CreateDistribution. Set " +
			"TT_CLOUDFRONT_CERT_ARN (us-east-1 cert ARN) and TT_CLOUDFRONT_ALIAS (a domain the cert " +
			"covers and the account controls) to run the apply tests. These are environment-level " +
			"prerequisites (a validated cert + Route53 zone) not creatable by this self-contained example.")
	}
	return certArn, alias
}

// testAlbVars returns the alb-origin variable set using a real validated certificate ARN
// and owned alias from the apply prerequisites. web_acl_id is omitted so the example
// self-provisions a real CLOUDFRONT-scope WAF Web ACL.
func testAlbVars(suffix, certArn, alias string) map[string]interface{} {
	return map[string]interface{}{
		"acm_certificate_arn": certArn,
		"aliases":             []string{alias},
		"log_bucket_suffix":   suffix,
		"alb_origin_domain":   "alb-example.us-east-1.elb.amazonaws.com",
	}
}

// testS3Vars returns the s3-origin variable set using a real validated certificate ARN
// and owned alias from the apply prerequisites. web_acl_id is omitted so the example
// self-provisions a real CLOUDFRONT-scope WAF Web ACL.
func testS3Vars(suffix, certArn, alias string) map[string]interface{} {
	return map[string]interface{}{
		"acm_certificate_arn": certArn,
		"aliases":             []string{alias},
		"bucket_name_suffix":  suffix,
	}
}

// TestCommonTerraformVersionAlbOrigin asserts Terraform version satisfies the pinned constraint for alb-origin.
func TestCommonTerraformVersionAlbOrigin(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-cmn-ver-alb-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonTerraformVersionS3Origin asserts Terraform version satisfies the pinned constraint for s3-origin.
func TestCommonTerraformVersionS3Origin(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-cmn-ver-s3-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3Vars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
}

// TestCommonRequiredOutputsAlbOrigin asserts all required outputs are present and non-empty for alb-origin.
func TestCommonRequiredOutputsAlbOrigin(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-cmn-out-alb-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_id"), "distribution_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_arn"), "distribution_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_domain_name"), "distribution_domain_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_hosted_zone_id"), "distribution_hosted_zone_id must not be empty")
}

// TestCommonRequiredOutputsS3Origin asserts all required outputs are present and non-empty for s3-origin.
func TestCommonRequiredOutputsS3Origin(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-cmn-out-s3-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3Vars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_id"), "distribution_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_arn"), "distribution_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_domain_name"), "distribution_domain_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_hosted_zone_id"), "distribution_hosted_zone_id must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "oac_id"), "oac_id must not be empty for S3 origin")
}

// TestCommonIdempotencyAlbOrigin asserts the alb-origin example is idempotent (plan after apply shows no changes).
func TestCommonIdempotencyAlbOrigin(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-cmn-idem-alb-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_distribution", 1)
	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_response_headers_policy", 1)
}

// TestCommonIdempotencyS3Origin asserts the s3-origin example is idempotent.
func TestCommonIdempotencyS3Origin(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-cmn-idem-s3-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3Vars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_distribution", 1)
	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_origin_access_control", 1)
}

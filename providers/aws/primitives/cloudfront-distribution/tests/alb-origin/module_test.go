//go:build terratest

package alb_origin_test

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


// cloudFrontApplyPrereqs gates the apply-based tests. Creating a CloudFront
// distribution requires a viewer certificate that is (a) issued by a publicly-trusted
// CA and (b) covers the alias (CNAME); CloudFront rejects self-signed/imported certs
// and reserved alias domains. A trusted, DNS-validated certificate plus an owned alias
// domain is an environment-level prerequisite that cannot be provisioned from a
// self-contained terratest. The apply tests therefore require the operator to supply a
// real validated certificate ARN (us-east-1) via TT_CLOUDFRONT_CERT_ARN and a matching
// owned alias via TT_CLOUDFRONT_ALIAS, and skip with an actionable message otherwise.
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

// testAlbOriginVars returns the standard variable set for the alb-origin example using a
// real validated certificate ARN and owned alias supplied via the apply prerequisites.
// web_acl_id is omitted so the example self-provisions a real CLOUDFRONT-scope WAF Web ACL.
func testAlbOriginVars(suffix, certArn, alias string) map[string]interface{} {
	return map[string]interface{}{
		"acm_certificate_arn": certArn,
		"aliases":             []string{alias},
		"log_bucket_suffix":   suffix,
		"alb_origin_domain":   "alb-example.us-east-1.elb.amazonaws.com",
	}
}

// TestAlbOriginDistributionDomainNameFormat asserts distribution_domain_name matches \.cloudfront\.net$.
func TestAlbOriginDistributionDomainNameFormat(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-alb-domain-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbOriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	domainName := terraform.Output(t, ctx.Terraform, "distribution_domain_name")
	assert.Regexp(t, `\.cloudfront\.net$`, domainName, "distribution_domain_name must match \\.cloudfront\\.net$")
}

// TestAlbOriginDistributionARNFormat asserts distribution_arn matches ^arn:aws:cloudfront::[0-9]{12}:distribution/.
func TestAlbOriginDistributionARNFormat(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-alb-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbOriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	distributionArn := terraform.Output(t, ctx.Terraform, "distribution_arn")
	assert.Regexp(t, `^arn:aws:cloudfront::[0-9]{12}:distribution/`, distributionArn, "distribution_arn must match ^arn:aws:cloudfront::[0-9]{12}:distribution/")
}

// TestAlbOriginDistributionIDNotEmpty asserts distribution_id is non-empty.
func TestAlbOriginDistributionIDNotEmpty(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-alb-id-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbOriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_id"), "distribution_id must not be empty")
}

// TestAlbOriginCloudFrontDistributionCount asserts aws_cloudfront_distribution count is 1.
func TestAlbOriginCloudFrontDistributionCount(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-alb-dist-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbOriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_distribution", 1)
}

// TestAlbOriginResponseHeadersPolicyCount asserts aws_cloudfront_response_headers_policy count is 1.
func TestAlbOriginResponseHeadersPolicyCount(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "alb-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-alb-rhp-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, testAlbOriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_response_headers_policy", 1)
}

// TestAlbOriginSubTLSVersionRejected asserts plan fails when minimum_protocol_version is below TLSv1.2_2021.
// Covers TLSv1.1_2016, TLSv1_2016, TLSv1.2_2018, and TLSv1.2_2019 -- all must be rejected per DoD requirement.
func TestAlbOriginSubTLSVersionRejected(t *testing.T) {
	rejectedVersions := []string{"TLSv1.1_2016", "TLSv1_2016", "TLSv1.2_2018", "TLSv1.2_2019"}
	for _, version := range rejectedVersions {
		version := version
		t.Run(version, func(t *testing.T) {
			suffix := fmt.Sprintf("%d", time.Now().Unix())
			// Plan-only negative test: a syntactically valid us-east-1 cert ARN is supplied so
			// the only validation that fails is the sub-TLSv1.2_2021 minimum_protocol_version.
			vars := map[string]interface{}{
				"acm_certificate_arn":      fmt.Sprintf("arn:aws:acm:us-east-1:123456789012:certificate/%s", suffix),
				"aliases":                  []string{fmt.Sprintf("collector-%s.example.com", suffix)},
				"alb_origin_domain":        "alb-example.us-east-1.elb.amazonaws.com",
				"minimum_protocol_version": version,
				"log_bucket_suffix":        suffix,
			}
			opts := testctx.InitTerraform("../../examples/alb-origin", testctx.TestConfig{
				Name:      fmt.Sprintf("cf-alb-tls-fail-%s-%s", version, suffix),
				ExtraVars: vars,
			})

			_, err := terraform.InitAndPlanE(t, opts)
			assert.Error(t, err, "plan must fail when minimum_protocol_version is %s (below TLSv1.2_2021)", version)
		})
	}
}

// TestAlbOriginEmptyCertARNRejected asserts plan fails when acm_certificate_arn is empty and aliases are set.
func TestAlbOriginEmptyCertARNRejected(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/alb-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-alb-empty-cert-%s", suffix),
		ExtraVars: map[string]interface{}{
			"acm_certificate_arn": "",
			"web_acl_id":          "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test/00000000-0000-0000-0000-000000000001",
			"aliases":             []string{fmt.Sprintf("collector-%s.example.com", suffix)},
			"log_bucket_suffix":   suffix,
			"alb_origin_domain":   "alb-example.us-east-1.elb.amazonaws.com",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when acm_certificate_arn is empty")
}

// TestAlbOriginNonUsEast1CertARNRejected asserts plan fails when acm_certificate_arn is not in us-east-1.
func TestAlbOriginNonUsEast1CertARNRejected(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/alb-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-alb-nonuse1-cert-%s", suffix),
		ExtraVars: map[string]interface{}{
			"acm_certificate_arn": fmt.Sprintf("arn:aws:acm:eu-west-1:123456789012:certificate/%s", suffix),
			"web_acl_id":          "arn:aws:wafv2:us-east-1:123456789012:global/webacl/test/00000000-0000-0000-0000-000000000001",
			"aliases":             []string{fmt.Sprintf("collector-%s.example.com", suffix)},
			"log_bucket_suffix":   suffix,
			"alb_origin_domain":   "alb-example.us-east-1.elb.amazonaws.com",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when acm_certificate_arn region is not us-east-1")
}

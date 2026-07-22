//go:build terratest

package s3_origin_test

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

// testS3OriginVars returns the standard variable set for the s3-origin example using a
// real validated certificate ARN and owned alias supplied via the apply prerequisites.
// web_acl_id is omitted so the example self-provisions a real CLOUDFRONT-scope WAF Web ACL.
func testS3OriginVars(suffix, certArn, alias string) map[string]interface{} {
	return map[string]interface{}{
		"acm_certificate_arn": certArn,
		"aliases":             []string{alias},
		"bucket_name_suffix":  suffix,
	}
}

// TestS3OriginOACCount asserts aws_cloudfront_origin_access_control count is 1.
func TestS3OriginOACCount(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-s3-oac-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3OriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_cloudfront_origin_access_control", 1)
}

// TestS3OriginOACIDNotEmpty asserts oac_id output is non-empty.
func TestS3OriginOACIDNotEmpty(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-s3-oac-id-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3OriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "oac_id"), "oac_id must not be empty for S3 origin")
}

// TestS3OriginDistributionIDNotEmpty asserts distribution_id is non-empty.
func TestS3OriginDistributionIDNotEmpty(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-s3-dist-id-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3OriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "distribution_id"), "distribution_id must not be empty")
}

// TestS3OriginDistributionDomainNameFormat asserts distribution_domain_name matches \.cloudfront\.net$.
func TestS3OriginDistributionDomainNameFormat(t *testing.T) {
	certArn, alias := cloudFrontApplyPrereqs(t)
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "s3-origin", testctx.TestConfig{
		Name:      fmt.Sprintf("cf-s3-domain-%s", suffix),
		ExtraVars: mergeExtraVars(t, testS3OriginVars(suffix, certArn, alias), mustTaggingVars(t)),
	})

	domainName := terraform.Output(t, ctx.Terraform, "distribution_domain_name")
	assert.Regexp(t, `\.cloudfront\.net$`, domainName, "distribution_domain_name must match \\.cloudfront\\.net$")
}

// TestS3OriginSubTLSVersionRejected asserts plan fails when minimum_protocol_version is below TLSv1.2_2021.
// Covers TLSv1_2016, TLSv1.1_2016, TLSv1.2_2018, and TLSv1.2_2019 -- all must be rejected per DoD requirement.
func TestS3OriginSubTLSVersionRejected(t *testing.T) {
	rejectedVersions := []string{"TLSv1_2016", "TLSv1.1_2016", "TLSv1.2_2018", "TLSv1.2_2019"}
	for _, version := range rejectedVersions {
		version := version
		t.Run(version, func(t *testing.T) {
			suffix := fmt.Sprintf("%d", time.Now().Unix())
			// Plan-only negative test: a syntactically valid us-east-1 cert ARN is supplied so
			// the only validation that fails is the sub-TLSv1.2_2021 minimum_protocol_version.
			vars := map[string]interface{}{
				"acm_certificate_arn":      fmt.Sprintf("arn:aws:acm:us-east-1:123456789012:certificate/%s", suffix),
				"aliases":                  []string{fmt.Sprintf("portal-%s.example.com", suffix)},
				"bucket_name_suffix":       suffix,
				"minimum_protocol_version": version,
			}
			opts := testctx.InitTerraform("../../examples/s3-origin", testctx.TestConfig{
				Name:      fmt.Sprintf("cf-s3-tls-fail-%s-%s", version, suffix),
				ExtraVars: vars,
			})

			_, err := terraform.InitAndPlanE(t, opts)
			assert.Error(t, err, "plan must fail when minimum_protocol_version is %s (below TLSv1.2_2021)", version)
		})
	}
}

// TestS3OriginEmptyCertARNRejected asserts plan fails when acm_certificate_arn is empty and aliases are set.
func TestS3OriginEmptyCertARNRejected(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/s3-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-s3-empty-cert-%s", suffix),
		ExtraVars: map[string]interface{}{
			"acm_certificate_arn": "",
			"aliases":             []string{fmt.Sprintf("portal-%s.example.com", suffix)},
			"bucket_name_suffix":  suffix,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when acm_certificate_arn is empty")
}

// TestS3OriginNonUsEast1CertARNRejected asserts plan fails when acm_certificate_arn is not in us-east-1.
func TestS3OriginNonUsEast1CertARNRejected(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/s3-origin", testctx.TestConfig{
		Name: fmt.Sprintf("cf-s3-nonuse1-cert-%s", suffix),
		ExtraVars: map[string]interface{}{
			"acm_certificate_arn": fmt.Sprintf("arn:aws:acm:ap-southeast-1:123456789012:certificate/%s", suffix),
			"aliases":             []string{fmt.Sprintf("portal-%s.example.com", suffix)},
			"bucket_name_suffix":  suffix,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when acm_certificate_arn region is not us-east-1")
}

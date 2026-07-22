//go:build terratest

package basic_test

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

// testDomain returns a unique subdomain under example-terratest.net.
// This domain is a real Route53 hosted zone in the sandbox account; ACM can
// complete DNS validation for subdomains without ADDITIONAL_VERIFICATION_REQUIRED
// (which example.com triggers in AWS provider v6.49.0+).
func testDomain(suffix string) string {
	return fmt.Sprintf("test-%s.example-terratest.net", suffix)
}

// baseExtraVars returns the common ExtraVars map for live apply tests.
// subject_alternative_names is cleared to the empty list so that no SAN entry
// from terraform.tfvars (which uses example.com) can trigger
// ADDITIONAL_VERIFICATION_REQUIRED in the sandbox account.
func baseExtraVars(suffix string) map[string]interface{} {
	return map[string]interface{}{
		"domain_name":               testDomain(suffix),
		"subject_alternative_names": []interface{}{},
	}
}

// TestACMCertificateCreation tests that an ACM certificate is created in us-east-1
// with DNS validation and correct outputs per docs/terragrunt-concepts.md. Also asserts
// Terraform version meets the minimum requirement and the plan is idempotent after apply.
func TestACMCertificateCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-acm-cert-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	assertions.AssertIdempotent(t, ctx)

	certArn := terraform.Output(t, ctx.Terraform, "certificate_arn")
	assert.Regexp(t, `^arn:aws:acm:us-east-1:[0-9]{12}:certificate/`, certArn, "certificate_arn must match us-east-1 ACM ARN pattern")
}

// TestACMCertificateDomainValidationOptions asserts that domain_validation_options is
// exposed and non-empty (validation_method defaults to DNS).
func TestACMCertificateDomainValidationOptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-acm-validation-method-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	domainValidationOptions := terraform.Output(t, ctx.Terraform, "domain_validation_options")
	assert.NotEmpty(t, domainValidationOptions, "domain_validation_options must be exposed and non-empty")
}

// TestACMCertificateResourceCounts asserts exactly one aws_acm_certificate and zero
// aws_acm_certificate_validation resources exist (validation belongs to consuming unit per D24).
func TestACMCertificateResourceCounts(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-acm-cert-counts-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_acm_certificate", 1)
	assertions.AssertResourceCount(t, ctx, "aws_acm_certificate_validation", 0)
}

// TestACMCertificateRequiredOutputsPresent asserts all required outputs are non-empty.
func TestACMCertificateRequiredOutputsPresent(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("basic-acm-cert-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "certificate_arn"), "certificate_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "domain_validation_options"), "domain_validation_options must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "certificate_domain_name"), "certificate_domain_name must not be empty")
}

// TestACMCertificateInvalidValidationMethod asserts that a validation_method outside DNS/EMAIL fails.
func TestACMCertificateInvalidValidationMethod(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-acm-bad-method-%s", suffix),
		ExtraVars: map[string]interface{}{
			"domain_name":               testDomain(suffix),
			"subject_alternative_names": []interface{}{},
			"validation_method":         "INVALID",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when validation_method is not DNS or EMAIL")
}

// TestACMCertificateInvalidKeyAlgorithm asserts that an unsupported key_algorithm fails validation.
func TestACMCertificateInvalidKeyAlgorithm(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-acm-bad-key-%s", suffix),
		ExtraVars: map[string]interface{}{
			"domain_name":               testDomain(suffix),
			"subject_alternative_names": []interface{}{},
			"key_algorithm":             "RSA_4096",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when key_algorithm is not one of RSA_2048, EC_prime256v1, EC_secp384r1")
}

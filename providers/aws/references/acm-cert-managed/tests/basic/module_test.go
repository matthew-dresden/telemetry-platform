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
// The cert is a pure request (wait_for_validation=false, no validation resource per
// D24), so the hosted zone need not exist; the subdomain only avoids the
// ADDITIONAL_VERIFICATION_REQUIRED that example.com triggers in AWS provider v6.49.0+.
func testDomain(suffix string) string {
	return fmt.Sprintf("test-%s.example-terratest.net", suffix)
}

// baseExtraVars returns the common ExtraVars map for live apply tests.
// subject_alternative_names is cleared to the empty list so that no SAN entry from
// terraform.tfvars (which uses example.com) can trigger ADDITIONAL_VERIFICATION_REQUIRED.
func baseExtraVars(suffix string) map[string]interface{} {
	return map[string]interface{}{
		"domain_name":               testDomain(suffix),
		"subject_alternative_names": []interface{}{},
	}
}

// TestACMCertManagedCreation applies the basic example and asserts the wrapped
// certificate is created in us-east-1 with the correct ARN shape, the Terraform version
// meets the minimum, and the plan is idempotent after apply.
func TestACMCertManagedCreation(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("acm-cert-managed-create-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	assertions.AssertIdempotent(t, ctx)

	certArn := terraform.Output(t, ctx.Terraform, "certificate_arn")
	assert.Regexp(t, `^arn:aws:acm:us-east-1:[0-9]{12}:certificate/`, certArn, "certificate_arn must match us-east-1 ACM ARN pattern")
}

// TestACMCertManagedDomainValidationOptions asserts that domain_validation_options is
// exposed and non-empty (validation_method defaults to DNS). This is the output the
// dns-owner pretty/validate units read cross-account.
func TestACMCertManagedDomainValidationOptions(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("acm-cert-managed-dvo-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	domainValidationOptions := terraform.Output(t, ctx.Terraform, "domain_validation_options")
	assert.NotEmpty(t, domainValidationOptions, "domain_validation_options must be exposed and non-empty")
}

// TestACMCertManagedResourceCounts asserts exactly one aws_acm_certificate (wrapped in
// the certificate child module) and zero aws_acm_certificate_validation resources exist
// (validation belongs to the consuming unit per D24).
func TestACMCertManagedResourceCounts(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("acm-cert-managed-counts-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_acm_certificate", 1)
	assertions.AssertResourceCount(t, ctx, "aws_acm_certificate_validation", 0)
}

// TestACMCertManagedGatedResourcesAbsentByDefault asserts that the cross-account
// remote-state read resources are gated: with the empty default inputs the module must
// create zero aws_kms_grant and zero aws_s3_bucket_policy (gate OFF; the standalone
// module performs no plan-time AWS read). The prod acm-collector/acm-portal leaf turns
// the bucket policy on.
func TestACMCertManagedGatedResourcesAbsentByDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("acm-cert-managed-gateoff-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_kms_grant", 0)
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_policy", 0)
}

// TestACMCertManagedRequiredOutputsPresent asserts all required outputs are non-empty.
func TestACMCertManagedRequiredOutputsPresent(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("acm-cert-managed-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, baseExtraVars(suffix), mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "certificate_arn"), "certificate_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "domain_validation_options"), "domain_validation_options must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "certificate_domain_name"), "certificate_domain_name must not be empty")
}

// TestACMCertManagedCrossAccountBucketPolicyGateOn is the ON-path counterpart to the
// gate-off count test. It plans the module root directly with the bucket-read gate
// inputs set and asserts the plan succeeds and would create the aws_s3_bucket_policy
// (the mechanism the prod acm leaves enable). The CMK-grant gate is left empty so the
// data.aws_kms_key read stays off and the plan needs no live CMK.
func TestACMCertManagedCrossAccountBucketPolicyGateOn(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("acm-cert-managed-gateon-%s", suffix),
		ExtraVars: map[string]interface{}{
			"domain_name":               testDomain(suffix),
			"subject_alternative_names": []interface{}{},
			"tfstate_bucket_name":       fmt.Sprintf("acm-cert-managed-test-%s-tfstate", suffix),
			"tfstate_bucket_account_id": "123456789012",
			"tfstate_bucket_read_grantee_arns": []interface{}{
				"arn:aws:iam::444444444444:role/telemetry-platform-gha-tg-plan",
			},
		},
	})

	planOutput, err := terraform.InitAndPlanE(t, opts)
	require.NoError(t, err, "plan must succeed when the bucket-read gate inputs are set")
	assert.Contains(t, planOutput, "aws_s3_bucket_policy.tfstate_cross_account_read",
		"plan must include the gated aws_s3_bucket_policy when tfstate_bucket_read_grantee_arns is set")
}

// TestACMCertManagedBucketPolicyPreconditionFailsWithoutAccountID asserts the fail-fast
// precondition: setting the bucket-read grantee without tfstate_bucket_account_id must
// fail the plan (no silent partial config).
func TestACMCertManagedBucketPolicyPreconditionFailsWithoutAccountID(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("acm-cert-managed-precond-%s", suffix),
		ExtraVars: map[string]interface{}{
			"domain_name":               testDomain(suffix),
			"subject_alternative_names": []interface{}{},
			"tfstate_bucket_name":       fmt.Sprintf("acm-cert-managed-test-%s-tfstate", suffix),
			"tfstate_bucket_read_grantee_arns": []interface{}{
				"arn:aws:iam::444444444444:role/telemetry-platform-gha-tg-plan",
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when tfstate_bucket_account_id is empty but a bucket-read grantee is set (fail-fast precondition)")
}

// TestACMCertManagedInvalidValidationMethod asserts that a validation_method outside
// DNS/EMAIL fails the plan (validation block in variables.tf).
func TestACMCertManagedInvalidValidationMethod(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("acm-cert-managed-bad-method-%s", suffix),
		ExtraVars: map[string]interface{}{
			"domain_name":               testDomain(suffix),
			"subject_alternative_names": []interface{}{},
			"validation_method":         "INVALID",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when validation_method is not DNS or EMAIL")
}

// TestACMCertManagedInvalidKeyAlgorithm asserts that an unsupported key_algorithm fails.
func TestACMCertManagedInvalidKeyAlgorithm(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("acm-cert-managed-bad-key-%s", suffix),
		ExtraVars: map[string]interface{}{
			"domain_name":               testDomain(suffix),
			"subject_alternative_names": []interface{}{},
			"key_algorithm":             "RSA_4096",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when key_algorithm is not one of RSA_2048, EC_prime256v1, EC_secp384r1")
}

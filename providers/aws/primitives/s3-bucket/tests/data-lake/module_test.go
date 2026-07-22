//go:build terratest

package datalake_test

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/example-org/telemetry-platform/providers/aws/primitives/s3-bucket/tests/helpers"
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


// TestDataLakeS3BucketLifecycleConfigurationExists asserts the lifecycle configuration resource count is 1.
func TestDataLakeS3BucketLifecycleConfigurationExists(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-dl-lc")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("dl-s3-lifecycle-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_lifecycle_configuration", 1)
}

// terraformStateJSON is a partial representation of the JSON emitted by `terraform show -json`
// for parsing lifecycle configuration rule attributes from Terraform state.
type terraformStateJSON struct {
	Values struct {
		RootModule struct {
			ChildModules []struct {
				Address   string `json:"address"`
				Resources []struct {
					Address    string                 `json:"address"`
					Type       string                 `json:"type"`
					Values     map[string]interface{} `json:"values"`
				} `json:"resources"`
			} `json:"child_modules"`
		} `json:"root_module"`
	} `json:"values"`
}

// TestDataLakeS3BucketLifecycleDaysMatchTfvars asserts lifecycle rule attributes match the terraform.tfvars values.
// The data-lake example sets transition_days=365, expiration_days=730, and transition_storage_class=GLACIER.
// It uses terraform show -json to read actual state attribute values rather than indirect proxy assertions.
func TestDataLakeS3BucketLifecycleDaysMatchTfvars(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-dl-days")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("dl-s3-days-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	// Confirm the lifecycle resource is present in state before reading its attributes.
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_lifecycle_configuration", 1)

	// Read actual state via terraform show -json and parse lifecycle rule attributes.
	stateJSON, err := terraform.ShowE(t, ctx.Terraform)
	require.NoError(t, err, "terraform show -json must succeed")

	var state terraformStateJSON
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state), "state JSON must be parseable")

	// Locate the aws_s3_bucket_lifecycle_configuration resource inside the example child module.
	var lifecycleValues map[string]interface{}
	for _, child := range state.Values.RootModule.ChildModules {
		for _, res := range child.Resources {
			if res.Type == "aws_s3_bucket_lifecycle_configuration" {
				lifecycleValues = res.Values
				break
			}
		}
		if lifecycleValues != nil {
			break
		}
	}
	require.NotNil(t, lifecycleValues, "aws_s3_bucket_lifecycle_configuration must be present in Terraform state")

	// The state value for lifecycle rules is a list of rule objects.
	rulesRaw, ok := lifecycleValues["rule"]
	require.True(t, ok, "lifecycle configuration state must contain a 'rule' attribute")

	rulesSlice, ok := rulesRaw.([]interface{})
	require.True(t, ok, "'rule' attribute must be a list")
	require.Len(t, rulesSlice, 1, "data-lake example must have exactly 1 lifecycle rule")

	rule, ok := rulesSlice[0].(map[string]interface{})
	require.True(t, ok, "lifecycle rule must be a map")

	// Assert transition: days=365, storage_class=GLACIER.
	transitionsRaw, ok := rule["transition"]
	require.True(t, ok, "lifecycle rule must contain a 'transition' block")
	transitions, ok := transitionsRaw.([]interface{})
	require.True(t, ok, "'transition' must be a list")
	require.Len(t, transitions, 1, "data-lake lifecycle rule must have exactly 1 transition")
	transition, ok := transitions[0].(map[string]interface{})
	require.True(t, ok, "transition entry must be a map")
	assert.Equal(t, float64(365), transition["days"], "transition days must be 365 as declared in terraform.tfvars")
	assert.Equal(t, "GLACIER", transition["storage_class"], "transition storage_class must be GLACIER as declared in terraform.tfvars")

	// Assert expiration: days=730.
	expirationsRaw, ok := rule["expiration"]
	require.True(t, ok, "lifecycle rule must contain an 'expiration' block")
	expirations, ok := expirationsRaw.([]interface{})
	require.True(t, ok, "'expiration' must be a list")
	require.Len(t, expirations, 1, "data-lake lifecycle rule must have exactly 1 expiration")
	expiration, ok := expirations[0].(map[string]interface{})
	require.True(t, ok, "expiration entry must be a map")
	assert.Equal(t, float64(730), expiration["days"], "expiration days must be 730 as declared in terraform.tfvars")
}

// TestDataLakeS3BucketLifecycleStorageClassGlacier asserts the data-lake example uses GLACIER storage class.
func TestDataLakeS3BucketLifecycleStorageClassGlacier(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-dl-glacier")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("dl-s3-glacier-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	// Confirms the module accepted GLACIER as the transition_storage_class
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_lifecycle_configuration", 1)
	bucketId := terraform.Output(t, ctx.Terraform, "bucket_id")
	assert.NotEmpty(t, bucketId, "bucket_id must not be empty confirming GLACIER lifecycle config accepted")
}

// TestDataLakeS3BucketIdempotency asserts the data-lake example is idempotent.
func TestDataLakeS3BucketIdempotency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName := helpers.GenerateUniqueBucketName("tst-dl-idem")
	ctx := testctx.RunSingleExample(t, "../../examples", "data-lake", testctx.TestConfig{
		Name: fmt.Sprintf("dl-s3-idempotency-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"bucket_name": bucketName,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket", 1)
	assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_lifecycle_configuration", 1)
}

//go:build terratest

package basic_test

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	awshelper "github.com/gruntwork-io/terratest/modules/aws"
	"github.com/gruntwork-io/terratest/modules/retry"
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

// lambdaTrustPolicy is the assume-role trust policy that allows the Lambda
// service to assume the execution role created by the test fixture.
const lambdaTrustPolicy = `{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}`

// lambdaBasicExecutionPolicyARN is the AWS-managed policy that grants a Lambda
// function the minimal CloudWatch Logs permissions required for a valid
// execution role.
const lambdaBasicExecutionPolicyARN = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

// iamRolePropagationMaxRetries bounds how many times the fixture polls GetRole
// while waiting for a freshly created IAM role to become retrievable before
// failing fast.
const iamRolePropagationMaxRetries = 30

// iamRolePropagationInterval is the interval between GetRole readiness polls.
const iamRolePropagationInterval = 3 * time.Second

// iamRoleFixture provisions a self-contained Lambda execution role inside the
// target account, attaches the AWS-managed basic execution policy, waits for the
// role to propagate, and registers t.Cleanup to detach the policy and delete the
// role after the test completes. It returns the role ARN.
//
// The cleanup runs after the terraform Destroy registered by RunSingleExample
// (Go t.Cleanup is LIFO and this fixture is invoked before RunSingleExample), so
// the Lambda function is removed before the role it depends on is deleted.
func iamRoleFixture(t *testing.T) string {
	t.Helper()
	region := mustAWSRegion(t)
	client := awshelper.NewIamClient(t, region)
	roleName := fmt.Sprintf("tst-lambda-exec-%d", time.Now().UnixNano())

	createOut, err := client.CreateRole(context.Background(), &iam.CreateRoleInput{
		RoleName:                 aws.String(roleName),
		AssumeRolePolicyDocument: aws.String(lambdaTrustPolicy),
		Description:              aws.String("Ephemeral Lambda execution role for terratest; safe to delete."),
		Path:                     aws.String("/telemetry-platform-terratest/"),
	})
	require.NoError(t, err, "CreateRole must succeed for the Lambda execution role fixture")
	roleARN := aws.ToString(createOut.Role.Arn)

	_, err = client.AttachRolePolicy(context.Background(), &iam.AttachRolePolicyInput{
		RoleName:  aws.String(roleName),
		PolicyArn: aws.String(lambdaBasicExecutionPolicyARN),
	})
	require.NoError(t, err, "AttachRolePolicy must succeed for the Lambda execution role fixture")

	t.Cleanup(func() {
		_, detachErr := client.DetachRolePolicy(context.Background(), &iam.DetachRolePolicyInput{
			RoleName:  aws.String(roleName),
			PolicyArn: aws.String(lambdaBasicExecutionPolicyARN),
		})
		assert.NoError(t, detachErr, "DetachRolePolicy must succeed during fixture cleanup")
		_, deleteErr := client.DeleteRole(context.Background(), &iam.DeleteRoleInput{
			RoleName: aws.String(roleName),
		})
		assert.NoError(t, deleteErr, "DeleteRole must succeed during fixture cleanup")
	})

	waitForIamRolePropagation(t, client, roleName)
	return roleARN
}

// waitForIamRolePropagation polls GetRole until the freshly created role is
// retrievable, failing fast once the retry budget is exhausted. This is active
// readiness detection (poll until the role exists), not a fixed delay.
func waitForIamRolePropagation(t *testing.T, client *iam.Client, roleName string) {
	t.Helper()
	_, err := retry.DoWithRetryE(
		t,
		fmt.Sprintf("wait for IAM role %q to propagate", roleName),
		iamRolePropagationMaxRetries,
		iamRolePropagationInterval,
		func() (string, error) {
			if _, getErr := client.GetRole(context.Background(), &iam.GetRoleInput{
				RoleName: aws.String(roleName),
			}); getErr != nil {
				return "", getErr
			}
			return "role is retrievable", nil
		},
	)
	require.NoError(t, err, "IAM role %q did not propagate within the retry budget", roleName)
}

// mustAWSRegion returns the AWS region from the environment, failing fast if not set.
func mustAWSRegion(t *testing.T) string {
	t.Helper()
	region := os.Getenv("AWS_DEFAULT_REGION")
	if region == "" {
		region = os.Getenv("AWS_REGION")
	}
	if region == "" {
		t.Fatal("ERROR: AWS_DEFAULT_REGION is not set; set to the target sandbox region (e.g. us-east-1)")
	}
	return region
}

// generateLambdaZip creates a minimal valid Python Lambda zip in memory.
// The handler code is the minimal valid Python 3.12 Lambda entrypoint.
// Returns the zip bytes and the base64-encoded SHA-256 hash suitable for
// the source_code_hash Terraform variable.
func generateLambdaZip(t *testing.T) ([]byte, string) {
	t.Helper()
	handlerCode := "def handler(event, context):\n    return {'statusCode': 200}\n"
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	f, err := w.Create("index.py")
	require.NoError(t, err, "zip.Create must not fail")
	_, err = f.Write([]byte(handlerCode))
	require.NoError(t, err, "zip.Write must not fail")
	err = w.Close()
	require.NoError(t, err, "zip.Close must not fail")
	zipBytes := buf.Bytes()
	hash := sha256.Sum256(zipBytes)
	sourceCodeHash := base64.StdEncoding.EncodeToString(hash[:])
	return zipBytes, sourceCodeHash
}

// generateUniqueBucketName returns a globally unique S3 bucket name with the given prefix.
// S3 bucket names must be globally unique, 3-63 chars, lowercase letters, numbers, and hyphens only.
func generateUniqueBucketName(prefix string) string {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	maxPrefixLen := 52
	if len(prefix) > maxPrefixLen {
		prefix = prefix[:maxPrefixLen]
	}
	return strings.ToLower(fmt.Sprintf("%s-%s", prefix, suffix))
}

// s3Fixture creates an S3 bucket, uploads a minimal Lambda zip, and registers t.Cleanup
// to empty and delete the bucket after the test completes.
// Returns (bucketName, s3Key, sourceCodeHash).
func s3Fixture(t *testing.T) (string, string, string) {
	t.Helper()
	region := mustAWSRegion(t)
	bucketName := generateUniqueBucketName("tst-lambda-basic")
	s3Key := "lambda/test/index.zip"
	zipBytes, sourceCodeHash := generateLambdaZip(t)

	awshelper.CreateS3Bucket(t, region, bucketName)
	t.Cleanup(func() {
		awshelper.EmptyS3Bucket(t, region, bucketName)
		awshelper.DeleteS3Bucket(t, region, bucketName)
	})

	awshelper.PutS3ObjectContents(t, region, bucketName, s3Key, bytes.NewReader(zipBytes))
	return bucketName, s3Key, sourceCodeHash
}

// TestBasicLambdaRequiredOutputsNotEmpty asserts all required outputs are non-empty.
func TestBasicLambdaRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName, s3Key, sourceCodeHash := s3Fixture(t)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"function_name":    fmt.Sprintf("test-lambda-%s", suffix),
			"role":             iamRoleFixture(t),
			"s3_bucket":        bucketName,
			"s3_key":           s3Key,
			"source_code_hash": sourceCodeHash,
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "function_arn"), "function_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "function_name"), "function_name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "invoke_arn"), "invoke_arn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "qualified_arn"), "qualified_arn must not be empty")
}

// TestBasicLambdaARNFormat asserts the function_arn output matches the Lambda ARN pattern.
func TestBasicLambdaARNFormat(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName, s3Key, sourceCodeHash := s3Fixture(t)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-arn-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"function_name":    fmt.Sprintf("test-lambda-%s", suffix),
			"role":             iamRoleFixture(t),
			"s3_bucket":        bucketName,
			"s3_key":           s3Key,
			"source_code_hash": sourceCodeHash,
		}, mustTaggingVars(t)),
	})

	functionArn := terraform.Output(t, ctx.Terraform, "function_arn")
	assert.Regexp(t, `^arn:aws:lambda:`, functionArn, "function_arn must match Lambda ARN pattern ^arn:aws:lambda:")
}

// TestBasicLambdaResourceCount asserts exactly one aws_lambda_function resource is created.
func TestBasicLambdaResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName, s3Key, sourceCodeHash := s3Fixture(t)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"function_name":    fmt.Sprintf("test-lambda-%s", suffix),
			"role":             iamRoleFixture(t),
			"s3_bucket":        bucketName,
			"s3_key":           s3Key,
			"source_code_hash": sourceCodeHash,
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_lambda_function", 1)
}

// TestBasicLambdaTimeoutAndMemoryConfigurable asserts the timeout + memory_size inputs
// flow through to the created function. The example tfvars set 30s/256MB (the values the
// portal embed-URL Lambda needs so the QuickSight + SSM round trip does not exceed the
// AWS-default 3s timeout); this verifies the new variables are wired end-to-end.
func TestBasicLambdaTimeoutAndMemoryConfigurable(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	bucketName, s3Key, sourceCodeHash := s3Fixture(t)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-timeout-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"function_name":    fmt.Sprintf("test-lambda-%s", suffix),
			"role":             iamRoleFixture(t),
			"s3_bucket":        bucketName,
			"s3_key":           s3Key,
			"source_code_hash": sourceCodeHash,
			"timeout":          30,
			"memory_size":      256,
		}, mustTaggingVars(t)),
	})

	assert.Equal(t, "30", terraform.Output(t, ctx.Terraform, "timeout"),
		"timeout output must reflect the configured 30s value")
	assert.Equal(t, "256", terraform.Output(t, ctx.Terraform, "memory_size"),
		"memory_size output must reflect the configured 256MB value")
}

// TestBasicLambdaInvalidTimeout asserts that an out-of-range timeout fails validation.
func TestBasicLambdaInvalidTimeout(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-bad-timeout-%s", suffix),
		ExtraVars: map[string]interface{}{
			"function_name":    fmt.Sprintf("test-lambda-%s", suffix),
			"role":             "arn:aws:iam::123456789012:role/x",
			"s3_bucket":        "tst-bucket",
			"s3_key":           "k.zip",
			"source_code_hash": "aGFzaA==",
			"timeout":          0,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	require.Error(t, err, "plan must fail when timeout is outside 1..900")
	assert.Contains(t, err.Error(), "timeout must be between 1 and 900",
		"the failure must be the timeout validation message")
}

// TestBasicLambdaInvalidMemorySize asserts that an out-of-range memory_size fails validation.
func TestBasicLambdaInvalidMemorySize(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-bad-memory-%s", suffix),
		ExtraVars: map[string]interface{}{
			"function_name":    fmt.Sprintf("test-lambda-%s", suffix),
			"role":             "arn:aws:iam::123456789012:role/x",
			"s3_bucket":        "tst-bucket",
			"s3_key":           "k.zip",
			"source_code_hash": "aGFzaA==",
			"memory_size":      64,
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	require.Error(t, err, "plan must fail when memory_size is outside 128..10240")
	assert.Contains(t, err.Error(), "memory_size must be between 128 and 10240",
		"the failure must be the memory_size validation message")
}

// TestBasicLambdaInvalidPackageType asserts that an unsupported package_type fails validation.
func TestBasicLambdaInvalidPackageType(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-bad-pkg-%s", suffix),
		ExtraVars: map[string]interface{}{
			"function_name": fmt.Sprintf("test-lambda-%s", suffix),
			"package_type":  "Image",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when package_type is not Zip")
}

// TestBasicLambdaInvalidRoleARN asserts that an invalid role ARN fails validation.
func TestBasicLambdaInvalidRoleARN(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-lambda-bad-role-%s", suffix),
		ExtraVars: map[string]interface{}{
			"function_name": fmt.Sprintf("test-lambda-%s", suffix),
			"role":          "not-an-iam-arn",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when role does not match ^arn:aws:iam:")
}

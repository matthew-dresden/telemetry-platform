//go:build terratest

package default_test

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/athena"
	athenatypes "github.com/aws/aws-sdk-go-v2/service/athena/types"
	"github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs"
	cwltypes "github.com/aws/aws-sdk-go-v2/service/cloudwatchlogs/types"
	"github.com/aws/aws-sdk-go-v2/service/firehose"
	firehosetypes "github.com/aws/aws-sdk-go-v2/service/firehose/types"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	s3types "github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/retry"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Firehose end-to-end delivery polling bounds (BUG-4 proof). The composed stream's
// default buffering interval is 300s, and the CloudWatch Logs subscription hop adds a
// few seconds, so a single record can take up to ~5-6 min to be delivered to S3. The
// poll timeout is overridable via the FIREHOSE_E2E_DELIVERY_TIMEOUT_SECONDS environment
// variable; the poll interval is the readiness-detection cadence (the test never sleeps
// to "wait", it polls S3 for the delivered object until it appears or the deadline is
// hit, then fails fast). The subscription-filter create retry covers IAM-role propagation.
//
// The CloudWatch-Logs-split transform Lambda adds a SECOND buffering hop: the source CWL
// records are buffered then handed to the Lambda, which re-ingests the split single-event
// records via firehose:PutRecordBatch, and those re-ingested records are buffered AGAIN
// before delivery. With the stream's 300s buffering interval that is up to ~2x300s plus
// processing, so the default delivery timeout is sized generously (overridable per env var).
const (
	firehoseDeliveryTimeoutEnvVar         = "FIREHOSE_E2E_DELIVERY_TIMEOUT_SECONDS"
	firehoseDeliveryTimeoutDefaultSeconds = 1080
	firehoseDeliveryPollIntervalSeconds   = 15
	subscriptionFilterCreateMaxRetries    = 24
	subscriptionFilterCreateIntervalSecs  = 5

	// Large-batch BUG-6 proof: a single CloudWatch Logs subscription record batching this
	// many events exceeds the native RecordDeAggregation 500-sub-record cap, so it proves the
	// re-ingestion Lambda splits any-size delivery. Overridable via env var.
	firehoseLargeBatchSizeEnvVar  = "FIREHOSE_E2E_LARGE_BATCH_SIZE"
	firehoseLargeBatchSizeDefault = 2000
	// PutLogEvents is capped at 10000 events / ~1MB per call; chunk well under both.
	cloudWatchPutLogEventsChunk = 1000
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

// pushTelemetryBatch emits n flat telemetry events into the bridge log group. Each event
// carries the given tool (the field the MetadataExtraction {tool:.tool} JQ reads to drive the
// tool=<x> partition), the given event_type (the non-partition data column the Athena value
// assertion verifies reads back non-NULL), and a unique payload per event. CloudWatch Logs
// batches the events into a single GZIP subscription record and forwards it to Firehose, where
// the transform Lambda must split the >500-event record into individual single-JSON records.
// Events are pushed in chunks under the PutLogEvents 10000-event / ~1MB-per-call limits, with
// the sequence token threaded between calls.
func pushTelemetryBatch(t *testing.T, ctxBg context.Context, logsClient *cloudwatchlogs.Client, logGroupName, logStreamName, tool, eventType string, n int) {
	t.Helper()

	var sequenceToken *string
	baseMillis := time.Now().UnixMilli() - int64(n)
	for start := 0; start < n; start += cloudWatchPutLogEventsChunk {
		end := start + cloudWatchPutLogEventsChunk
		if end > n {
			end = n
		}
		events := make([]cwltypes.InputLogEvent, 0, end-start)
		for i := start; i < end; i++ {
			event := map[string]interface{}{
				"tool":       tool,
				"timestamp":  time.Now().UTC().Format(time.RFC3339),
				"event_type": eventType,
				"payload":    fmt.Sprintf("evt-%d", i),
			}
			eventJSON, err := json.Marshal(event)
			require.NoError(t, err, "marshalling the telemetry event JSON must succeed")
			events = append(events, cwltypes.InputLogEvent{
				Message:   aws.String(string(eventJSON)),
				Timestamp: aws.Int64(baseMillis + int64(i)),
			})
		}
		out, err := logsClient.PutLogEvents(ctxBg, &cloudwatchlogs.PutLogEventsInput{
			LogGroupName:  aws.String(logGroupName),
			LogStreamName: aws.String(logStreamName),
			LogEvents:     events,
			SequenceToken: sequenceToken,
		})
		require.NoError(t, err, "PutLogEvents to the telemetry log group must succeed")
		sequenceToken = out.NextSequenceToken
	}
}

// pushStructuredRecord emits a single structured OTLP-shaped CWL log event into the bridge
// log group -- the REAL shape the collector's raw_log=false exporter produces
// (`{body,attributes,resource,scope}`, no top-level `tool` key), as opposed to
// pushTelemetryBatch's flat `{tool,timestamp,event_type,payload}` envelopes which already
// pass through the cwl_split transform Lambda byte-identical. This is the RESHAPE proof: the
// Lambda must map resource.service.name -> tool via SERVICE_TOOL_MAP, take event_type
// from the event.name attribute, and flatten every other attribute onto payload with dots
// normalized to underscores, before the record ever reaches MetadataExtraction/Parquet
// conversion.
func pushStructuredRecord(t *testing.T, ctxBg context.Context, logsClient *cloudwatchlogs.Client, logGroupName, logStreamName, messageJSON string) {
	t.Helper()

	_, err := logsClient.PutLogEvents(ctxBg, &cloudwatchlogs.PutLogEventsInput{
		LogGroupName:  aws.String(logGroupName),
		LogStreamName: aws.String(logStreamName),
		LogEvents: []cwltypes.InputLogEvent{
			{
				Message:   aws.String(messageJSON),
				Timestamp: aws.Int64(time.Now().UnixMilli()),
			},
		},
	})
	require.NoError(t, err, "PutLogEvents of the structured record must succeed")
}

// bridgeCloudWatchLogsToFirehose stands up the real telemetry ingestion path for the
// end-to-end proof: a dedicated CloudWatch Logs log group plus a match-all subscription
// filter that forwards to the data lake's Firehose stream (mirroring the collector's
// awscloudwatchlogs hop). The stream's Decompression + CloudWatchLogProcessing processors
// reject direct firehose:PutRecord (InvalidSourceException -- "not originating from
// CloudWatch"), so a record can only be pushed through this CloudWatch Logs origin. The
// returned log group + log stream names are where the caller emits the event. All created
// resources are torn down via t.Cleanup (which runs when this subtest returns -- before the
// framework's terraform destroy, so the filter is gone before the stream is destroyed).
//
// The delivery stream has server_side_encryption with the telemetry-data CMK
// (CUSTOMER_MANAGED_CMK), so the subscription delivery role must hold
// kms:GenerateDataKey + kms:Decrypt on that CMK in addition to firehose:PutRecord --
// otherwise PutSubscriptionFilter's test-message delivery fails with
// InvalidParameterException "Could not deliver test message to specified Firehose stream"
// and the filter is never created. This mirrors the production collector-ingestion
// CWL-to-Firehose role (references/collector-ingestion FirehoseStreamCmkAccess); the CMK
// key policy delegates to the account root, so this IAM grant alone is sufficient.
func bridgeCloudWatchLogsToFirehose(t *testing.T, ctxBg context.Context, cfg aws.Config, region, streamName, streamARN, cmkARN string) (logGroupName, logStreamName string) {
	t.Helper()

	iamClient := iam.NewFromConfig(cfg)
	logsClient := cloudwatchlogs.NewFromConfig(cfg)
	fhClient := firehose.NewFromConfig(cfg)

	suffix := time.Now().UnixNano()
	// tt- prefix so the daily terratest IAM sweep safety-net (role/tt-*) reclaims the role
	// if t.Cleanup is ever skipped (e.g. a hard process kill).
	roleName := fmt.Sprintf("tt-dl-cwl-e2e-%d", suffix)
	logGroupName = fmt.Sprintf("/tt/dl-cwl-e2e/%d", suffix)
	logStreamName = fmt.Sprintf("e2e-%d", suffix)
	filterName := fmt.Sprintf("tt-dl-cwl-e2e-%d", suffix)
	inlinePolicyName := "firehose-put"

	// Readiness: the stream must be ACTIVE before a subscription filter can target it.
	require.Eventuallyf(t, func() bool {
		out, err := fhClient.DescribeDeliveryStream(ctxBg, &firehose.DescribeDeliveryStreamInput{
			DeliveryStreamName: aws.String(streamName),
		})
		return err == nil && out.DeliveryStreamDescription != nil &&
			out.DeliveryStreamDescription.DeliveryStreamStatus == firehosetypes.DeliveryStreamStatusActive
	}, 2*time.Minute, 5*time.Second, "Firehose stream %s must reach ACTIVE before subscribing", streamName)

	// IAM role CloudWatch Logs assumes to put the subscription records to Firehose.
	trustPolicy := fmt.Sprintf(`{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"logs.%s.amazonaws.com"},"Action":"sts:AssumeRole"}]}`, region)
	createRoleOut, err := iamClient.CreateRole(ctxBg, &iam.CreateRoleInput{
		RoleName:                 aws.String(roleName),
		AssumeRolePolicyDocument: aws.String(trustPolicy),
		Description:              aws.String("terratest BUG-4 e2e: CloudWatch Logs -> Firehose subscription delivery role"),
	})
	require.NoError(t, err, "creating the CloudWatch Logs subscription delivery role must succeed")
	t.Cleanup(func() {
		_, _ = iamClient.DeleteRolePolicy(ctxBg, &iam.DeleteRolePolicyInput{RoleName: aws.String(roleName), PolicyName: aws.String(inlinePolicyName)})
		_, _ = iamClient.DeleteRole(ctxBg, &iam.DeleteRoleInput{RoleName: aws.String(roleName)})
	})
	roleARN := aws.ToString(createRoleOut.Role.Arn)

	// firehose:PutRecord(Batch) on the stream PLUS kms:GenerateDataKey/Decrypt on the
	// telemetry-data CMK: the stream is SSE-encrypted with that CMK, so a producer that
	// lacks the KMS grant cannot deliver (PutSubscriptionFilter's test message fails with
	// "Could not deliver test message to specified Firehose stream"). Mirrors the production
	// collector-ingestion CWL-to-Firehose role.
	putPolicy := fmt.Sprintf(`{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["firehose:PutRecord","firehose:PutRecordBatch"],"Resource":"%s"},{"Effect":"Allow","Action":["kms:GenerateDataKey","kms:Decrypt"],"Resource":"%s"}]}`, streamARN, cmkARN)
	_, err = iamClient.PutRolePolicy(ctxBg, &iam.PutRolePolicyInput{
		RoleName:       aws.String(roleName),
		PolicyName:     aws.String(inlinePolicyName),
		PolicyDocument: aws.String(putPolicy),
	})
	require.NoError(t, err, "attaching the firehose:PutRecord + CMK inline policy must succeed")

	_, err = logsClient.CreateLogGroup(ctxBg, &cloudwatchlogs.CreateLogGroupInput{LogGroupName: aws.String(logGroupName)})
	require.NoError(t, err, "creating the e2e log group must succeed")
	t.Cleanup(func() {
		_, _ = logsClient.DeleteLogGroup(ctxBg, &cloudwatchlogs.DeleteLogGroupInput{LogGroupName: aws.String(logGroupName)})
	})

	_, err = logsClient.CreateLogStream(ctxBg, &cloudwatchlogs.CreateLogStreamInput{
		LogGroupName:  aws.String(logGroupName),
		LogStreamName: aws.String(logStreamName),
	})
	require.NoError(t, err, "creating the e2e log stream must succeed")

	// PutSubscriptionFilter validates the role by sending a CONTROL_MESSAGE test record to
	// Firehose; it fails until the freshly-created role has propagated, so retry. The
	// CONTROL_MESSAGE is dropped by CloudWatchLogProcessing, so it never lands an object.
	_, subErr := retry.DoWithRetryE(
		t,
		fmt.Sprintf("create subscription filter %s -> %s", filterName, streamName),
		subscriptionFilterCreateMaxRetries,
		time.Duration(subscriptionFilterCreateIntervalSecs)*time.Second,
		func() (string, error) {
			_, e := logsClient.PutSubscriptionFilter(ctxBg, &cloudwatchlogs.PutSubscriptionFilterInput{
				LogGroupName:   aws.String(logGroupName),
				FilterName:     aws.String(filterName),
				FilterPattern:  aws.String(""),
				DestinationArn: aws.String(streamARN),
				RoleArn:        aws.String(roleARN),
			})
			return "", e
		},
	)
	require.NoError(t, subErr, "creating the CloudWatch Logs -> Firehose subscription filter must succeed")
	t.Cleanup(func() {
		_, _ = logsClient.DeleteSubscriptionFilter(ctxBg, &cloudwatchlogs.DeleteSubscriptionFilterInput{
			LogGroupName: aws.String(logGroupName),
			FilterName:   aws.String(filterName),
		})
	})

	return logGroupName, logStreamName
}

// TestDataLakeDefault applies the default example once, runs the framework-default
// idempotency re-plan, then exercises all assertions as subtests before destroy.
// All subtests share the same applied Terraform context via the parent test's t.Cleanup.
func TestDataLakeDefault(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())

	// Generate the unique, S3-path-safe per-run tool value BEFORE apply so it can be added
	// to the Glue table's enum partition-projection values (glue_partition_projection_tool_values).
	// The tool partition is an ENUM projection: Athena only projects partitions for the
	// enumerated tool values, so the end-to-end Athena delivery proof's per-run tool MUST be in
	// the enum set at apply time -- otherwise its delivered raw/tool=<unique>/ partitions are
	// never projected and the count query returns 0. The value is threaded to the subtests below
	// via this enclosing-scope variable (each t.Run body is a closure over it) instead of being
	// regenerated after apply. toolProjectionValues carries the governed defaults plus this run's
	// unique tool, and is asserted verbatim against projection.tool.values.
	//
	// claudeTool is the second unique per-run tool value, reserved for the cwl_split
	// transform Lambda's structured-record RESHAPE proof: service_tool_map (below) maps a
	// structured record's resource.service.name=claude-code (a real, currently-registered
	// structured-OTLP tool, used here as the concrete example) to this value, so it must
	// ALSO be enumerated in the enum tool projection before apply, exactly like uniqueTool,
	// or its reshaped raw/tool=<claudeTool>/ partitions would never be projected.
	uniqueTool := fmt.Sprintf("terratest%d", time.Now().UnixNano())
	claudeTool := fmt.Sprintf("claude-code-terratest%d", time.Now().UnixNano())
	toolProjectionValues := []string{"e2e-smoke", "example-cli", uniqueTool, claudeTool}

	extraVars := mustTaggingVars(t)
	extraVars["glue_partition_projection_tool_values"] = toolProjectionValues
	// RESHAPE (cwl_split transform Lambda): a structured record's
	// resource.service.name="claude-code" reshapes to tool=claudeTool. Inert for every other
	// message (example-cli/e2e-smoke pass through byte-identical because they carry a top-level
	// tool key), so this addition cannot affect any other subtest's assertions.
	extraVars["service_tool_map"] = map[string]string{"claude-code": claudeTool}

	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("data-lake-default-%s", suffix),
		ExtraVars: extraVars,
	})

	// AC-1: firehose_delivery_stream_arn is non-empty and matches Firehose ARN pattern.
	t.Run("FirehoseDeliveryStreamArnOutput", func(t *testing.T) {
		arn := terraform.Output(t, ctx.Terraform, "firehose_delivery_stream_arn")
		assert.NotEmpty(t, arn, "firehose_delivery_stream_arn must not be empty")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:firehose:[a-z0-9-]+:[0-9]{12}:deliverystream/`),
			arn,
			"firehose_delivery_stream_arn must match Firehose ARN pattern",
		)
	})

	// AC-1: firehose_stream_name is non-empty and equals the composed Firehose
	// primitive's resource name. Consumed by the observability unit as the
	// DeliveryStreamName alarm dimension (one-directional dependency edge, D37);
	// without this output the observability plan/apply fails with
	// "This object does not have an attribute named firehose_stream_name".
	t.Run("FirehoseStreamNameOutput", func(t *testing.T) {
		name := terraform.Output(t, ctx.Terraform, "firehose_stream_name")
		assert.NotEmpty(t, name, "firehose_stream_name must not be empty")
		assert.Contains(t, name, "telemetry-events",
			"firehose_stream_name must be the namespace-scoped Firehose stream name (suffix -telemetry-events)")
	})

	// AC-1: data_lake_bucket_arn is non-empty and matches S3 bucket ARN pattern.
	t.Run("BucketArnOutput", func(t *testing.T) {
		arn := terraform.Output(t, ctx.Terraform, "data_lake_bucket_arn")
		assert.NotEmpty(t, arn, "data_lake_bucket_arn must not be empty")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:s3:::`),
			arn,
			"data_lake_bucket_arn must match S3 ARN pattern",
		)
	})

	// AC-1: glue_database_name is non-empty.
	t.Run("GlueDatabaseNameOutput", func(t *testing.T) {
		name := terraform.Output(t, ctx.Terraform, "glue_database_name")
		assert.NotEmpty(t, name, "glue_database_name must not be empty")
	})

	// AC-1 + AC-3: lake_kms_key_arn is non-empty and matches KMS ARN pattern.
	t.Run("LakeKmsKeyArnOutput", func(t *testing.T) {
		arn := terraform.Output(t, ctx.Terraform, "lake_kms_key_arn")
		assert.NotEmpty(t, arn, "lake_kms_key_arn must not be empty")
		assert.Regexp(t,
			regexp.MustCompile(`^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/`),
			arn,
			"lake_kms_key_arn must match KMS key ARN pattern",
		)
	})

	// AC-3: exactly 2 aws_iam_role resources -- the Firehose delivery role and the
	// CloudWatch-Logs-split transform Lambda's execution role. A SINGLE Firehose delivery role
	// serves BOTH the delivery role_arn AND the data_format_conversion
	// schema_configuration.role_arn -- there is no separate Glue conversion role, because a
	// glue.amazonaws.com-only role cannot be assumed by Firehose (CreateDeliveryStream
	// InvalidArgumentException: "Access was denied when assuming role ... in the data format
	// conversion configuration"). The second role is the Lambda execution role (trusts
	// lambda.amazonaws.com), required for the record-splitting transform processor.
	t.Run("IamRolesComposed", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_iam_role", 2)
	})

	// ROOT-CAUSE GUARD: the Firehose data_format_conversion schema_configuration role
	// MUST be the Firehose delivery role (which trusts firehose.amazonaws.com), NOT a
	// glue.amazonaws.com-only role. Firehose assumes the schema_configuration role to
	// read the Glue Data Catalog during Parquet conversion; a Glue-only trust caused
	// CreateDeliveryStream to fail with InvalidArgumentException. This subtest pins the
	// fix by asserting the schema_configuration.role_arn in the created Firehose stream
	// equals the composed Firehose delivery role ARN.
	t.Run("SchemaConfigurationRoleEqualsFirehoseDeliveryRole", func(t *testing.T) {
		firehoseRoleArn := terraform.Output(t, ctx.Terraform, "firehose_role_arn")
		require.NotEmpty(t, firehoseRoleArn, "firehose_role_arn output must be present")
		require.Regexp(t,
			regexp.MustCompile(`^arn:aws:iam::[0-9]{12}:role/`),
			firehoseRoleArn,
			"firehose_role_arn must be a valid IAM role ARN",
		)

		schemaRoleArn := findSchemaConfigurationRoleArn(t, ctx)
		require.NotEmpty(t, schemaRoleArn,
			"the created Firehose stream must have a data_format_conversion schema_configuration role_arn")
		assert.Equal(t, firehoseRoleArn, schemaRoleArn,
			"schema_configuration.role_arn must equal the Firehose delivery role ARN so Firehose can assume it for format conversion")
	})

	// ROOT-CAUSE GUARD: the schema_configuration role (== the Firehose delivery role)
	// MUST trust the firehose.amazonaws.com service principal, otherwise Firehose
	// cannot assume it to read the Glue Data Catalog ("Access was denied when assuming
	// role"). This asserts the delivery role trust policy names firehose.amazonaws.com.
	t.Run("FirehoseDeliveryRoleTrustsFirehoseService", func(t *testing.T) {
		trustPolicyJSON := terraform.Output(t, ctx.Terraform, "firehose_assume_role_policy_json")
		require.NotEmpty(t, trustPolicyJSON, "firehose_assume_role_policy_json output must be present")

		var policy map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(trustPolicyJSON), &policy),
			"firehose_assume_role_policy_json must be valid JSON")

		statements, ok := policy["Statement"].([]interface{})
		require.True(t, ok, "trust policy Statement must be an array")

		trustsFirehose := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "sts:AssumeRole") {
				continue
			}
			principal, ok := stmt["Principal"].(map[string]interface{})
			if !ok {
				continue
			}
			services := extractStringSlice(principal["Service"])
			if containsAny(services, "firehose.amazonaws.com") {
				trustsFirehose = true
			}
		}
		assert.True(t, trustsFirehose,
			"the Firehose delivery role (also the schema_configuration role) trust policy must allow firehose.amazonaws.com to sts:AssumeRole")
	})

	// AC-3: Firehose delivery role inline policy S3 statement is scoped to the
	// lake bucket ARN + /* only, not to * (docs/terragrunt-concepts.md).
	t.Run("FirehoseRoleS3StatementScopedToBucketOnly", func(t *testing.T) {
		bucketArn := terraform.Output(t, ctx.Terraform, "data_lake_bucket_arn")
		require.NotEmpty(t, bucketArn, "data_lake_bucket_arn must be present")

		statements := getFirehoseRolePolicyStatements(t, ctx)

		foundS3 := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "s3:PutObject", "s3:GetBucketLocation") {
				continue
			}
			foundS3 = true
			resources := extractStringSlice(stmt["Resource"])
			assert.NotEmpty(t, resources, "S3 statement must have non-empty Resource list")
			for _, r := range resources {
				assert.NotEqual(t, "*", r, "S3 statement Resource must NOT be wildcard *")
				assert.True(t,
					strings.HasPrefix(r, bucketArn),
					"S3 statement Resource %q must be scoped to lake bucket %q", r, bucketArn,
				)
			}
		}
		assert.True(t, foundS3, "Firehose delivery role inline policy must contain an S3 statement")
	})

	// AC-3: kms statement in the Firehose delivery role inline policy carries
	// kms:ViaService = s3.<region>.amazonaws.com (docs/terragrunt-concepts.md).
	t.Run("FirehoseRoleKmsStatementHasViaServiceCondition", func(t *testing.T) {
		awsRegion := terraform.Output(t, ctx.Terraform, "aws_region")
		require.NotEmpty(t, awsRegion, "aws_region output must be present for ViaService assertion")
		expectedViaService := fmt.Sprintf("s3.%s.amazonaws.com", awsRegion)

		statements := getFirehoseRolePolicyStatements(t, ctx)

		foundKms := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "kms:GenerateDataKey", "kms:Decrypt") {
				continue
			}
			foundKms = true

			condition, ok := stmt["Condition"].(map[string]interface{})
			require.True(t, ok, "KMS statement must have a Condition block")

			stringEquals, ok := condition["StringEquals"].(map[string]interface{})
			require.True(t, ok, "KMS statement Condition must contain StringEquals")

			viaService, ok := stringEquals["kms:ViaService"].(string)
			require.True(t, ok, "KMS statement Condition StringEquals must contain kms:ViaService")
			assert.Equal(t, expectedViaService, viaService,
				"kms:ViaService must be %s per docs/terragrunt-concepts.md", expectedViaService)
		}
		assert.True(t, foundKms, "Firehose delivery role inline policy must contain a KMS statement")
	})

	// AC-3: Glue statement in the Firehose delivery role is scoped to a specific
	// Glue table ARN, not * (AC-3).
	t.Run("FirehoseRoleGlueStatementScopedToTableOnly", func(t *testing.T) {
		statements := getFirehoseRolePolicyStatements(t, ctx)

		foundGlue := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions") {
				continue
			}
			foundGlue = true
			resources := extractStringSlice(stmt["Resource"])
			assert.NotEmpty(t, resources, "Glue statement must have non-empty Resource list")
			for _, r := range resources {
				assert.NotEqual(t, "*", r, "Glue statement Resource must NOT be wildcard *")
				assert.True(t,
					strings.HasPrefix(r, "arn:aws:glue:"),
					"Glue statement Resource %q must be a Glue ARN", r,
				)
			}
		}
		assert.True(t, foundGlue, "Firehose delivery role inline policy must contain a Glue statement")
	})

	// AC-3: logs:PutLogEvents statement in the Firehose delivery role is scoped to the
	// specific log group ARN, not * (AC-3).
	t.Run("FirehoseRoleLogsStatementScopedToLogGroupOnly", func(t *testing.T) {
		statements := getFirehoseRolePolicyStatements(t, ctx)

		foundLogs := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "logs:PutLogEvents") {
				continue
			}
			foundLogs = true
			resources := extractStringSlice(stmt["Resource"])
			assert.NotEmpty(t, resources, "Logs statement must have non-empty Resource list")
			for _, r := range resources {
				assert.NotEqual(t, "*", r, "Logs statement Resource must NOT be wildcard *")
				assert.True(t,
					strings.HasPrefix(r, "arn:aws:logs:"),
					"Logs statement Resource %q must be a CloudWatch Logs ARN", r,
				)
			}
		}
		assert.True(t, foundLogs, "Firehose delivery role inline policy must contain a logs:PutLogEvents statement")
	})

	// AC-3: the composed telemetry-data CMK has enable_key_rotation=true
	// (docs/terragrunt-concepts.md) verified via terraform show -json.
	t.Run("KmsKeyEnableRotation", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_kms_key", 1)

		stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
		require.NoError(t, err, "terraform show -json must succeed to inspect kms key rotation attribute")
		require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

		var state map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
			"terraform show -json output must be valid JSON")

		values, ok := state["values"].(map[string]interface{})
		require.True(t, ok, "state JSON must contain a 'values' object")

		rootModule, ok := values["root_module"].(map[string]interface{})
		require.True(t, ok, "state values must contain a 'root_module' object")

		rotationEnabled := findKmsKeyRotationEnabled(t, rootModule)
		assert.True(t, rotationEnabled,
			"aws_kms_key enable_key_rotation must be true per docs/terragrunt-concepts.md")
	})

	// AC-3: the telemetry-data CMK policy contains no wildcard Principal: "*" entry
	// (docs/terragrunt-concepts.md).
	t.Run("KmsKeyPolicyNoWildcardPrincipal", func(t *testing.T) {
		kmsKeyPolicyJSON := terraform.Output(t, ctx.Terraform, "lake_kms_key_policy_json")
		require.NotEmpty(t, kmsKeyPolicyJSON, "lake_kms_key_policy_json must be exposed for wildcard assertion")

		assert.NotContains(t, kmsKeyPolicyJSON, `"Principal": "*"`,
			"KMS key policy must not contain wildcard principal")
		assert.NotContains(t, kmsKeyPolicyJSON, `"Principal":"*"`,
			"KMS key policy must not contain wildcard principal (compact form)")

		var policy map[string]interface{}
		err := json.Unmarshal([]byte(kmsKeyPolicyJSON), &policy)
		require.NoError(t, err, "lake_kms_key_policy_json must be valid JSON")

		statements, ok := policy["Statement"].([]interface{})
		require.True(t, ok, "KMS key policy Statement must be an array")

		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			principal := stmt["Principal"]
			if principalStr, ok := principal.(string); ok {
				assert.NotEqual(t, "*", principalStr,
					"KMS key policy must not contain wildcard Principal: \"*\"")
			}
		}
	})

	// docs/terragrunt-concepts.md: the telemetry-data CMK encrypts the Firehose delivery-stream
	// CloudWatch log group, so the CMK policy MUST grant the CloudWatch Logs service
	// principal logs.<region>.amazonaws.com the log-group CMK actions under an ArnLike
	// kms:EncryptionContext:aws:logs:arn condition. Without this, CreateLogGroup fails
	// with AccessDeniedException ("KMS key does not exist or is not allowed to be used").
	t.Run("KmsKeyPolicyGrantsCloudWatchLogs", func(t *testing.T) {
		awsRegion := terraform.Output(t, ctx.Terraform, "aws_region")
		require.NotEmpty(t, awsRegion, "aws_region output must be present for CloudWatch Logs grant assertion")
		expectedLogsPrincipal := fmt.Sprintf("logs.%s.amazonaws.com", awsRegion)

		kmsKeyPolicyJSON := terraform.Output(t, ctx.Terraform, "lake_kms_key_policy_json")
		require.NotEmpty(t, kmsKeyPolicyJSON, "lake_kms_key_policy_json must be exposed for CloudWatch Logs grant assertion")

		var policy map[string]interface{}
		err := json.Unmarshal([]byte(kmsKeyPolicyJSON), &policy)
		require.NoError(t, err, "lake_kms_key_policy_json must be valid JSON")

		statements, ok := policy["Statement"].([]interface{})
		require.True(t, ok, "KMS key policy Statement must be an array")

		requiredActions := []string{
			"kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey",
		}

		logsGrantFound := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			principal, ok := stmt["Principal"].(map[string]interface{})
			if !ok {
				continue
			}
			if svc, ok := principal["Service"].(string); !ok || svc != expectedLogsPrincipal {
				continue
			}
			logsGrantFound = true

			actions := extractStringSlice(stmt["Action"])
			for _, want := range requiredActions {
				assert.Contains(t, actions, want,
					"CloudWatch Logs statement must grant %s; got %v", want, actions)
			}

			cond, ok := stmt["Condition"].(map[string]interface{})
			require.True(t, ok, "CloudWatch Logs statement must carry a Condition")
			arnLike, ok := cond["ArnLike"].(map[string]interface{})
			require.True(t, ok, "CloudWatch Logs statement Condition must use ArnLike")
			ctxARN, ok := arnLike["kms:EncryptionContext:aws:logs:arn"].(string)
			require.True(t, ok, "ArnLike must key on kms:EncryptionContext:aws:logs:arn")
			assert.Contains(t, ctxARN, ":log-group:",
				"encryption-context ARN must scope to log groups")
		}
		assert.True(t, logsGrantFound,
			"telemetry-data CMK policy must grant %s the CloudWatch Logs CMK actions", expectedLogsPrincipal)
	})

	// AC-11 / BUG-4: firehose_dynamic_partitioning_jq is passed through to the composed
	// Firehose primitive AND the rendered MetadataExtractionQuery is a valid JQ expression
	// that extracts only the `tool` partition key. The previous default carried a second
	// key `date = "%Y/%m/%d"`; that strftime token is NOT valid JQ, so the merged
	// MetadataExtractionQuery ({date:%Y/%m/%d,tool:.tool}) failed to compile, dynamic
	// partitioning failed, and every delivered record landed under
	// errors/metadata-extraction-failed/ instead of raw/tool=.../dt=.../ -- the data lake
	// went dark. The dt partition comes from the Firehose-native !{timestamp:<fmt>} prefix
	// namespace, never from JQ. This subtest fails on any strftime token reaching the query.
	t.Run("FirehoseMetadataExtractionQueryIsValidJQ", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)

		strftimeToken := regexp.MustCompile(`%[A-Za-z]`)

		partitioningMap := terraform.OutputMap(t, ctx.Terraform, "firehose_dynamic_partitioning_jq_output")
		require.NotEmpty(t, partitioningMap, "firehose_dynamic_partitioning_jq_output must be non-empty")

		toolExpr, hasToolKey := partitioningMap["tool"]
		assert.True(t, hasToolKey, "partitioning map must contain 'tool' key")
		assert.Equal(t, ".tool", toolExpr, "partitioning map 'tool' value must be the JQ path .tool")

		_, hasDateKey := partitioningMap["date"]
		assert.False(t, hasDateKey,
			"partitioning map must NOT contain a 'date' key: its strftime value (%%Y/%%m/%%d) is invalid JQ and sends every record to errors/ (BUG-4); the dt partition comes from the Firehose-native !{timestamp:<fmt>} prefix namespace, not JQ")

		for key, expr := range partitioningMap {
			assert.NotEmpty(t, expr, "partitioning map %q value must be a non-empty JQ expression", key)
			assert.NotRegexp(t, strftimeToken, expr,
				"partitioning map %q value %q contains a strftime token; only valid JQ is allowed (the dt partition is supplied by !{timestamp:<fmt>}, not JQ)", key, expr)
		}

		// The rendered, on-the-stream MetadataExtractionQuery is the real artifact AWS
		// compiles as JQ. Assert it is exactly {tool:.tool} and carries no strftime token.
		query := findFirehoseMetadataExtractionQuery(t, ctx)
		require.NotEmpty(t, query, "firehose MetadataExtractionQuery must be present on the delivery stream")
		assert.Equal(t, "{tool:.tool}", query,
			"rendered MetadataExtractionQuery must be exactly {tool:.tool} (valid JQ extracting only the tool partition)")
		assert.NotRegexp(t, strftimeToken, query,
			"rendered MetadataExtractionQuery %q must not contain a strftime token -- that is invalid JQ and routes records to errors/ (BUG-4)", query)
	})

	// BUG-6 + BUG-7 end-to-end proof: push a LARGE batch (>= firehoseLargeBatchSizeDefault,
	// default 2000) through the REAL telemetry ingestion path (a CloudWatch Logs log group +
	// match-all subscription filter -> this Firehose stream, mirroring the collector's
	// awscloudwatchlogs hop) and prove every event is delivered to raw/ (NONE to
	// errors/metadata-extraction-failed/) AND that Athena reads the actual row values.
	//
	// A single CloudWatch Logs subscription record batches the whole burst, which exceeds the
	// native RecordDeAggregation 500-sub-record cap. Before the fix that whole multi-object
	// blob was passed to the MetadataExtraction JQ engine, which rejected it with "Non JSON
	// record provided" and routed every event to errors/ (BUG-6, proven in qa). The
	// re-ingestion transform Lambda splits the delivery into individual single-JSON records
	// (no 500 cap), so all of them reach raw/tool=<x>/dt=<y>/*.parquet.
	//
	// Athena then verifies the Parquet is queryable: before the fix the Glue table used the
	// OpenX JSON SerDe over binary Parquet, so every data column read back NULL (BUG-7). With
	// the ParquetHiveSerDe the SELECT returns the real values and the row count equals the
	// number of events sent. A direct firehose:PutRecord is impossible here -- the stream's
	// Lambda processor / CloudWatch origin contract means records must flow through the
	// CloudWatch Logs subscription origin.
	t.Run("FirehoseLargeBatchSplitsToRawAndAthenaQueryable", func(t *testing.T) {
		streamName := terraform.Output(t, ctx.Terraform, "firehose_stream_name")
		require.NotEmpty(t, streamName, "firehose_stream_name output required for the end-to-end delivery proof")
		streamARN := terraform.Output(t, ctx.Terraform, "firehose_delivery_stream_arn")
		require.NotEmpty(t, streamARN, "firehose_delivery_stream_arn output required for the subscription destination")
		bucketARN := terraform.Output(t, ctx.Terraform, "data_lake_bucket_arn")
		require.NotEmpty(t, bucketARN, "data_lake_bucket_arn output required for the end-to-end delivery proof")
		region := terraform.Output(t, ctx.Terraform, "aws_region")
		require.NotEmpty(t, region, "aws_region output required to construct the AWS clients")
		cmkARN := terraform.Output(t, ctx.Terraform, "lake_kms_key_arn")
		require.NotEmpty(t, cmkARN, "lake_kms_key_arn output required: the SSE-CMK delivery role needs kms:GenerateDataKey on it")
		database := terraform.Output(t, ctx.Terraform, "glue_database_name")
		require.NotEmpty(t, database, "glue_database_name output required for the Athena query proof")
		table := terraform.Output(t, ctx.Terraform, "glue_table_name")
		require.NotEmpty(t, table, "glue_table_name output required for the Athena query proof")

		bucketName := strings.TrimPrefix(bucketARN, "arn:aws:s3:::")
		require.NotEqual(t, bucketARN, bucketName, "data_lake_bucket_arn must be an S3 ARN of the form arn:aws:s3:::<name>")

		batchSize := firehoseLargeBatchSizeDefault
		if raw := os.Getenv(firehoseLargeBatchSizeEnvVar); raw != "" {
			parsed, parseErr := strconv.Atoi(raw)
			require.NoError(t, parseErr, "%s must be an integer", firehoseLargeBatchSizeEnvVar)
			require.Greater(t, parsed, 500, "%s must exceed the 500-sub-record deaggregation cap to prove the split", firehoseLargeBatchSizeEnvVar)
			batchSize = parsed
		}

		// The unique, S3-path-safe per-run tool value generated up front in the parent test and
		// injected into the Glue table's enum partition-projection values (glue_partition_projection_tool_values)
		// BEFORE apply, so the enum projects this run's raw/tool=<unique>/ partitions (an enum
		// projection never projects a tool value absent from the enumerated set). The delivered
		// objects land under their own partition that no other run can collide with, and a known
		// event_type gives the Athena value assertion a concrete expected value.
		tool := uniqueTool
		const expectedEventType = "terratest_large_batch"

		ctxBg := context.Background()
		cfg, err := config.LoadDefaultConfig(ctxBg, config.WithRegion(region))
		require.NoError(t, err, "loading AWS config must succeed for the end-to-end delivery proof")

		s3Client := s3.NewFromConfig(cfg)
		athenaClient := athena.NewFromConfig(cfg)

		// Baseline the errors/ object count BEFORE pushing so the assertion that the split
		// produced NO new failed records is run-scoped (errors/ is not tool-partitioned).
		errorsBaseline := countS3Objects(t, ctxBg, s3Client, bucketName, "errors/")

		// Stand up the CloudWatch Logs -> Firehose bridge (the real ingestion origin) and emit
		// the large batch. CloudWatch Logs gzips + wraps the events in ONE subscription record
		// (> 500 events), which the transform Lambda must split into individual records.
		logGroupName, logStreamName := bridgeCloudWatchLogsToFirehose(t, ctxBg, cfg, region, streamName, streamARN, cmkARN)
		logsClient := cloudwatchlogs.NewFromConfig(cfg)
		pushTelemetryBatch(t, ctxBg, logsClient, logGroupName, logStreamName, tool, expectedEventType, batchSize)

		// A dedicated, plain (SSE-S3) results bucket for Athena query output -- keeps query
		// results out of the KMS-encrypted data lake bucket and is torn down on cleanup.
		resultsLocation := newAthenaResultsBucket(t, ctxBg, s3Client, region)

		timeoutSeconds := firehoseDeliveryTimeoutDefaultSeconds
		if raw := os.Getenv(firehoseDeliveryTimeoutEnvVar); raw != "" {
			parsed, parseErr := strconv.Atoi(raw)
			require.NoError(t, parseErr, "%s must be an integer number of seconds", firehoseDeliveryTimeoutEnvVar)
			require.Greater(t, parsed, 0, "%s must be a positive number of seconds", firehoseDeliveryTimeoutEnvVar)
			timeoutSeconds = parsed
		}
		maxRetries := timeoutSeconds / firehoseDeliveryPollIntervalSeconds
		require.Greater(t, maxRetries, 0, "delivery timeout must allow at least one poll attempt")

		countQuery := fmt.Sprintf("SELECT count(*) FROM %q.%q WHERE tool = '%s'", database, table, tool)

		// Readiness detection: poll Athena (count of delivered rows for this run's tool) until
		// it equals the number of events sent or the bounded deadline is hit. Each poll also
		// fails fast if any record landed in errors/ (the split failed). The test never sleeps
		// to assume success; it asserts the actual delivered row count.
		_, pollErr := retry.DoWithRetryE(
			t,
			fmt.Sprintf("await %d split records under raw/tool=%s/ (Athena count)", batchSize, tool),
			maxRetries,
			time.Duration(firehoseDeliveryPollIntervalSeconds)*time.Second,
			func() (string, error) {
				if grown := countS3Objects(t, ctxBg, s3Client, bucketName, "errors/"); grown > errorsBaseline {
					return "", retry.FatalError{Underlying: fmt.Errorf(
						"%d new object(s) under errors/ -- the transform Lambda failed to split the delivery (BUG-6 regression)",
						grown-errorsBaseline)}
				}
				rows := runAthenaQuery(t, ctxBg, athenaClient, database, resultsLocation, countQuery)
				require.Len(t, rows, 1, "count query must return exactly one data row")
				got, convErr := strconv.Atoi(rows[0][0])
				if convErr != nil {
					return "", fmt.Errorf("count value %q is not an integer: %w", rows[0][0], convErr)
				}
				if got < batchSize {
					return "", fmt.Errorf("delivered row count %d < %d sent (still buffering)", got, batchSize)
				}
				return strconv.Itoa(got), nil
			},
		)
		if pollErr != nil {
			errKeys := listS3Keys(t, ctxBg, s3Client, bucketName, "errors/")
			t.Fatalf("the %d-event batch was NOT fully delivered+queryable within %ds (BUG-6/BUG-7). errors/ keys: %v",
				batchSize, timeoutSeconds, errKeys)
		}

		// BUG-6: NONE of the split records landed in errors/ (no new error objects this run).
		errorsAfter := countS3Objects(t, ctxBg, s3Client, bucketName, "errors/")
		assert.Equal(t, errorsBaseline, errorsAfter,
			"no new objects may appear under errors/ -- every split record must reach raw/ (BUG-6)")

		// BUG-6: the delivered objects are under raw/tool=<unique>/dt=<y>/ (not errors/).
		rawPrefix := fmt.Sprintf("raw/tool=%s/", tool)
		rawKeys := listS3Keys(t, ctxBg, s3Client, bucketName, rawPrefix)
		require.NotEmpty(t, rawKeys, "at least one delivered object must exist under %s", rawPrefix)
		assert.Contains(t, rawKeys[0], "/dt=",
			"delivered object key %q must carry the dt= partition segment (raw/tool=<x>/dt=<y>/)", rawKeys[0])

		// ENUM-PROJECTION REGRESSION GUARD: the tool partition is an ENUM projection, so the
		// table is queryable with NO tool filter -- a bare SELECT count(*) over the whole table
		// succeeds and returns at least the rows delivered under this run's tool. Under the
		// previous INJECTED tool projection this exact query was rejected with CONSTRAINT_VIOLATION
		// ("column 'tool' ... must be constrained by a WHERE equality"), so a passing no-filter
		// query is the direct proof that enum made the table queryable without a tool filter (so
		// SELECT * and BI tools work out of the box). runAthenaQuery fails the test if the query
		// does not reach SUCCEEDED, so a returned row set is itself the queryability assertion.
		noFilterCountQuery := fmt.Sprintf("SELECT count(*) FROM %q.%q", database, table)
		noFilterRows := runAthenaQuery(t, ctxBg, athenaClient, database, resultsLocation, noFilterCountQuery)
		require.Len(t, noFilterRows, 1, "the no-tool-filter count query must return exactly one data row")
		noFilterCount, noFilterConvErr := strconv.Atoi(noFilterRows[0][0])
		require.NoError(t, noFilterConvErr, "the no-tool-filter count value %q must be an integer", noFilterRows[0][0])
		assert.GreaterOrEqual(t, noFilterCount, batchSize,
			"a no-tool-filter SELECT count(*) must succeed under the enum tool projection and return >= the %d delivered rows (an injected tool column rejected this query with CONSTRAINT_VIOLATION)", batchSize)

		// BUG-7: Athena reads the ACTUAL Parquet values (not NULL). Assert the exact row count
		// equals the events sent AND the non-partition data column (event_type) reads back the
		// known value for every row -- which is impossible when a JSON SerDe misreads Parquet.
		valueQuery := fmt.Sprintf(
			"SELECT event_type, count(*) FROM %q.%q WHERE tool = '%s' GROUP BY event_type", database, table, tool)
		valueRows := runAthenaQuery(t, ctxBg, athenaClient, database, resultsLocation, valueQuery)
		require.Len(t, valueRows, 1,
			"every delivered row must carry the same non-NULL event_type (a JSON SerDe over Parquet would yield NULL/garbage, BUG-7)")
		assert.Equal(t, expectedEventType, valueRows[0][0],
			"Athena must read the actual event_type value written to Parquet, not NULL (BUG-7)")
		gotCount, convErr := strconv.Atoi(valueRows[0][1])
		require.NoError(t, convErr, "grouped count must be an integer")
		assert.Equal(t, batchSize, gotCount,
			"Athena row count for the non-NULL event_type must equal the %d events sent (BUG-6 split + BUG-7 queryable)", batchSize)
	})

	// RESHAPE end-to-end proof: a structured OTLP record (the REAL shape the
	// collector's raw_log=false exporter produces -- {body,attributes,resource,scope}, no
	// top-level tool key) is reshaped by the cwl_split transform Lambda into the canonical
	// {timestamp,tool,event_type,payload} envelope BEFORE MetadataExtraction/Parquet
	// conversion: resource.service.name="claude-code" is mapped to tool=claudeTool via
	// SERVICE_TOOL_MAP (service_tool_map, injected into extraVars above),
	// event_type is sourced from the event.name attribute (not the raw OTLP body), and every
	// other attribute is flattened onto payload with dots normalized to underscores
	// (marketplace.name -> marketplace_name, plugin.name -> plugin_name). Proving this
	// survives real Firehose -> Parquet conversion -> Glue -> Athena is the only way to catch
	// a reshape regression a Lambda-isolated unit test cannot: a wrong
	// SERVICE_TOOL_MAP wiring, a MetadataExtraction JQ mismatch against the reshaped
	// envelope, or a Parquet schema mismatch on the flattened payload.
	t.Run("StructuredRecordReshapedAndAthenaQueryable", func(t *testing.T) {
		streamName := terraform.Output(t, ctx.Terraform, "firehose_stream_name")
		require.NotEmpty(t, streamName, "firehose_stream_name output required for the end-to-end delivery proof")
		streamARN := terraform.Output(t, ctx.Terraform, "firehose_delivery_stream_arn")
		require.NotEmpty(t, streamARN, "firehose_delivery_stream_arn output required for the subscription destination")
		bucketARN := terraform.Output(t, ctx.Terraform, "data_lake_bucket_arn")
		require.NotEmpty(t, bucketARN, "data_lake_bucket_arn output required for the end-to-end delivery proof")
		region := terraform.Output(t, ctx.Terraform, "aws_region")
		require.NotEmpty(t, region, "aws_region output required to construct the AWS clients")
		cmkARN := terraform.Output(t, ctx.Terraform, "lake_kms_key_arn")
		require.NotEmpty(t, cmkARN, "lake_kms_key_arn output required: the SSE-CMK delivery role needs kms:GenerateDataKey on it")
		database := terraform.Output(t, ctx.Terraform, "glue_database_name")
		require.NotEmpty(t, database, "glue_database_name output required for the Athena query proof")
		table := terraform.Output(t, ctx.Terraform, "glue_table_name")
		require.NotEmpty(t, table, "glue_table_name output required for the Athena query proof")

		bucketName := strings.TrimPrefix(bucketARN, "arn:aws:s3:::")
		require.NotEqual(t, bucketARN, bucketName, "data_lake_bucket_arn must be an S3 ARN of the form arn:aws:s3:::<name>")

		// The unique, S3-path-safe per-run claude tool value generated up front in the parent
		// test and injected into BOTH service_tool_map (so resource.service.name
		// "claude-code" reshapes to this tool) AND the enum tool projection (so its
		// raw/tool=<claudeTool>/ partitions are projected and queryable). Unique markers
		// embedded in the attribute values make this run's row unambiguously findable.
		tool := claudeTool
		marketplaceName := fmt.Sprintf("%s-mk", tool)
		pluginName := fmt.Sprintf("%s-plugin", tool)

		// The REAL structured shape the collector's raw_log=false exporter produces
		// (captured empirically): body is the dotted event name, attributes carry the event
		// fields (dotted keys), resource.service.name is the structured-OTLP service the
		// Lambda maps via SERVICE_TOOL_MAP, scope names the emitting instrumentation.
		// No top-level tool field -- that is exactly what routes this message into the
		// reshape branch instead of the example-cli-style byte-identical passthrough.
		structuredRecord := map[string]interface{}{
			"body": "claude_code.plugin_loaded",
			"attributes": map[string]interface{}{
				"event.name":       "plugin_loaded",
				"event.timestamp":  time.Now().UTC().Format(time.RFC3339),
				"plugin.name":      pluginName,
				"marketplace.name": marketplaceName,
			},
			"resource": map[string]interface{}{"service.name": "claude-code"},
			"scope":    map[string]interface{}{"name": "com.anthropic.claude_code.events"},
		}
		recordJSON, err := json.Marshal(structuredRecord)
		require.NoError(t, err, "marshalling the structured record JSON must succeed")

		ctxBg := context.Background()
		cfg, err := config.LoadDefaultConfig(ctxBg, config.WithRegion(region))
		require.NoError(t, err, "loading AWS config must succeed for the end-to-end delivery proof")

		s3Client := s3.NewFromConfig(cfg)
		athenaClient := athena.NewFromConfig(cfg)
		logsClient := cloudwatchlogs.NewFromConfig(cfg)

		// Stand up a fresh CloudWatch Logs -> Firehose bridge (the real ingestion origin,
		// same mechanism the large-batch subtest above uses) and emit the single structured
		// record through it.
		logGroupName, logStreamName := bridgeCloudWatchLogsToFirehose(t, ctxBg, cfg, region, streamName, streamARN, cmkARN)
		pushStructuredRecord(t, ctxBg, logsClient, logGroupName, logStreamName, string(recordJSON))

		// A dedicated, plain (SSE-S3) results bucket for Athena query output.
		resultsLocation := newAthenaResultsBucket(t, ctxBg, s3Client, region)

		timeoutSeconds := firehoseDeliveryTimeoutDefaultSeconds
		if raw := os.Getenv(firehoseDeliveryTimeoutEnvVar); raw != "" {
			parsed, parseErr := strconv.Atoi(raw)
			require.NoError(t, parseErr, "%s must be an integer number of seconds", firehoseDeliveryTimeoutEnvVar)
			require.Greater(t, parsed, 0, "%s must be a positive number of seconds", firehoseDeliveryTimeoutEnvVar)
			timeoutSeconds = parsed
		}
		maxRetries := timeoutSeconds / firehoseDeliveryPollIntervalSeconds
		require.Greater(t, maxRetries, 0, "delivery timeout must allow at least one poll attempt")

		rawPrefix := fmt.Sprintf("raw/tool=%s/", tool)
		valueQuery := fmt.Sprintf(
			"SELECT event_type, json_extract_scalar(payload,'$.marketplace_name') AS mk, json_extract_scalar(payload,'$.plugin_name') AS pl FROM %q.%q WHERE tool = '%s'",
			database, table, tool)

		// Readiness detection: poll S3 for the delivered object under raw/tool=<claudeTool>/
		// (mirroring the large-batch subtest's delivery proof), then Athena for the reshaped
		// row -- the enum projection resolves this run's partition purely from the S3 key
		// layout, so once the object lands the row is immediately queryable. The test never
		// sleeps to assume success; it polls until the reshaped row appears or the bounded
		// deadline is hit, then fails fast.
		var valueRows [][]string
		_, pollErr := retry.DoWithRetryE(
			t,
			fmt.Sprintf("await the reshaped structured record under %s (Athena query)", rawPrefix),
			maxRetries,
			time.Duration(firehoseDeliveryPollIntervalSeconds)*time.Second,
			func() (string, error) {
				if len(listS3Keys(t, ctxBg, s3Client, bucketName, rawPrefix)) == 0 {
					return "", fmt.Errorf("no object delivered yet under %s (still buffering)", rawPrefix)
				}
				rows := runAthenaQuery(t, ctxBg, athenaClient, database, resultsLocation, valueQuery)
				if len(rows) == 0 {
					return "", fmt.Errorf("delivered object present under %s but not yet queryable (still buffering)", rawPrefix)
				}
				valueRows = rows
				return strconv.Itoa(len(rows)), nil
			},
		)
		if pollErr != nil {
			rawKeys := listS3Keys(t, ctxBg, s3Client, bucketName, rawPrefix)
			t.Fatalf("the structured record was NOT reshaped+delivered+queryable within %ds under %s. raw/ keys: %v",
				timeoutSeconds, rawPrefix, rawKeys)
		}

		// The reshape's three provable facts, read back through real Parquet + Glue + Athena:
		// (1) resource.service.name=claude-code mapped to tool=<claudeTool> via
		// SERVICE_TOOL_MAP -- proven by the WHERE tool='<claudeTool>' filter itself
		// returning a row; (2) event_type sourced from the event.name attribute, not the raw
		// OTLP body; (3) dot-to-underscore payload flattening (marketplace.name ->
		// marketplace_name, plugin.name -> plugin_name) survived Parquet conversion.
		require.Len(t, valueRows, 1, "exactly one reshaped row must be queryable for this run's unique claude tool")
		assert.Equal(t, "plugin_loaded", valueRows[0][0],
			"event_type must be reshaped from the event.name attribute (plugin_loaded), not the raw OTLP body")
		assert.Equal(t, marketplaceName, valueRows[0][1],
			"payload.marketplace_name must be the reshaped, underscore-flattened form of attributes[marketplace.name]")
		assert.Equal(t, pluginName, valueRows[0][2],
			"payload.plugin_name must be the reshaped, underscore-flattened form of attributes[plugin.name]")

		// ENUM proof: the unique claude tool value must itself be enumerated in the Glue
		// table's projection.tool.values (glue_partition_projection_tool_values) -- pinned
		// directly against the applied table parameter, independent of the query above having
		// already relied on it implicitly.
		params := terraform.OutputMap(t, ctx.Terraform, "glue_table_parameters")
		toolValues := params["projection.tool.values"]
		require.NotEmpty(t, toolValues, "projection.tool.values must be present and non-empty for an enum tool projection")
		assert.Contains(t, strings.Split(toolValues, ","), tool,
			"projection.tool.values %q must enumerate this run's unique claude tool %q", toolValues, tool)
	})

	// CloudWatch-Logs-split transform Lambda: the composed Firehose's processing_configuration
	// is Lambda -> MetadataExtraction. The Lambda is the record-splitting front end for the
	// collector's awscloudwatchlogs hop: a single CloudWatch Logs subscription record batches
	// MANY logEvents, and the AWS-native unwrap path (Decompression -> CloudWatchLogProcessing
	// -> RecordDeAggregation) fails for deliveries larger than 500 events because
	// RecordDeAggregation is hard-capped at 500 sub-records per record (a larger record is
	// passed WHOLE to the MetadataExtraction JQ engine, which rejects it with "Non JSON record
	// provided" and routes every event to errors/metadata-extraction-failed/, BUG-6). The Lambda
	// decompresses + envelope-strips each record and re-ingests each logEvents[].message as its
	// own single-JSON record via firehose:PutRecordBatch (no 500-record cap), so a delivery of
	// any size is split into individual JSON records before MetadataExtraction. This asserts the
	// processor ordering and that the Lambda processor's LambdaArn is the composed split Lambda.
	t.Run("FirehoseTransformLambdaProcessors", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)
		assertions.AssertResourceCount(t, ctx, "aws_lambda_function", 1)

		procTypes := findFirehoseProcessorTypes(t, ctx)
		require.NotEmpty(t, procTypes, "firehose processing_configuration must declare processors")
		assert.Equal(t, []string{"Lambda", "MetadataExtraction"}, procTypes,
			"CloudWatch-Logs-source telemetry stream must order processors Lambda -> MetadataExtraction")

		lambdaArn := terraform.Output(t, ctx.Terraform, "cwl_transform_lambda_arn")
		require.NotEmpty(t, lambdaArn, "cwl_transform_lambda_arn output must be present")
		require.Regexp(t,
			regexp.MustCompile(`^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:function:`),
			lambdaArn,
			"cwl_transform_lambda_arn must be a valid Lambda function ARN",
		)

		processorLambdaArn := findFirehoseProcessorParameter(t, ctx, "Lambda", "LambdaArn")
		assert.Equal(t, lambdaArn, processorLambdaArn,
			"the Firehose Lambda processor's LambdaArn must be the composed CloudWatch-Logs-split transform Lambda ARN")

		// D37: cwl_transform_lambda_name is the FunctionName CloudWatch alarm dimension the
		// observability unit consumes as a dependency output. It must be non-empty and must be
		// the bare function name embedded in cwl_transform_lambda_arn (arn:aws:lambda:<region>:
		// <account>:function:<name>), never a recomputed or hardcoded value.
		lambdaName := terraform.Output(t, ctx.Terraform, "cwl_transform_lambda_name")
		require.NotEmpty(t, lambdaName, "cwl_transform_lambda_name output must be present")
		assert.True(t, strings.HasSuffix(lambdaArn, "function:"+lambdaName),
			"cwl_transform_lambda_name must be the function name suffix of cwl_transform_lambda_arn")
	})

	// AC-11: S3 lifecycle transition_storage_class is propagated to the composed s3-bucket.
	t.Run("S3LifecycleTransitionStorageClassPropagated", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_lifecycle_configuration", 1)

		storageClass := terraform.Output(t, ctx.Terraform, "transition_storage_class")
		assert.Equal(t, "GLACIER", storageClass,
			"transition_storage_class output must equal the input value 'GLACIER' confirming propagation")
		assert.Contains(t,
			[]string{"GLACIER", "GLACIER_IR", "DEEP_ARCHIVE"},
			storageClass,
			"transition_storage_class must be a valid cold-tier storage class",
		)
	})

	// AC-11: Firehose-to-Glue data lake wiring resolves (both Glue database and
	// Firehose delivery stream are created).
	t.Run("FirehoseToGlueWiringResolves", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_database", 1)
		assertions.AssertResourceCount(t, ctx, "aws_kinesis_firehose_delivery_stream", 1)
	})

	// BUG-3 ROOT-CAUSE GUARD: the Glue format-conversion table's data columns and
	// partition keys MUST be disjoint. Glue stores partition keys as additional
	// descriptor columns, so a name that is both a data column and a partition key
	// (the original table declared `tool` as both) produces a descriptor with
	// duplicate columns that Athena/QuickSight reject on every query with
	// "HIVE_INVALID_METADATA: Table descriptor contains duplicate columns". This
	// asserts against the applied table that tool is a partition key ONLY, dt is a
	// partition key, and no data column name overlaps any partition key.
	t.Run("GlueTableHasNoDuplicateColumn", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_glue_catalog_table", 1)

		columns, partitionKeys := findGlueTableColumnsAndPartitionKeys(t, ctx)
		require.NotEmpty(t, columns, "the Glue table must declare data columns")
		require.NotEmpty(t, partitionKeys, "the Glue table must declare partition keys")

		assert.Contains(t, partitionKeys, "tool", "tool must be a partition key")
		assert.Contains(t, partitionKeys, "dt", "dt must be a partition key")
		assert.NotContains(t, columns, "tool",
			"tool must NOT be a data column -- it is a partition key only, read from the dynamic-partition path")

		for _, col := range columns {
			assert.NotContains(t, partitionKeys, col,
				"data column %q must not also be a partition key (Glue rejects the descriptor with HIVE_INVALID_METADATA duplicate columns)", col)
		}
	})

	// BUG-3: Athena partition projection must be enabled on the table so partitions
	// resolve at query time from the S3 key layout without a crawler or registered
	// partitions (the original table had no parameters, so even valid queries
	// returned nothing). tool is an ENUM dimension whose governed set of values is
	// input-driven (glue_partition_projection_tool_values): enum keeps partition
	// pruning scoped to those tools while leaving the table queryable with no tool
	// filter (SELECT * works), unlike an injected column which rejects any query
	// lacking a static WHERE tool='...' equality with CONSTRAINT_VIOLATION. dt is a
	// date dimension whose format must equal the Firehose !{timestamp:yyyy-MM-dd} path.
	t.Run("GlueTablePartitionProjectionEnabled", func(t *testing.T) {
		params := terraform.OutputMap(t, ctx.Terraform, "glue_table_parameters")
		require.NotEmpty(t, params, "glue_table_parameters must be non-empty (Athena partition projection configured)")

		assert.Equal(t, "true", params["projection.enabled"],
			"projection.enabled must be true so Athena resolves partitions without a crawler")
		assert.Equal(t, "enum", params["projection.tool.type"],
			"tool must be an enum projection dimension so the table is queryable with no tool filter (SELECT * works); an injected column would reject any query lacking a static WHERE tool='...' equality with CONSTRAINT_VIOLATION")

		// projection.tool.values must be present, non-empty, and enumerate every configured
		// tool value -- the governed defaults PLUS this run's unique tool (injected into
		// glue_partition_projection_tool_values before apply so the enum projects the
		// end-to-end proof's per-run partitions). The value is the comma-joined sorted list
		// the module builds from the input.
		toolValues := params["projection.tool.values"]
		require.NotEmpty(t, toolValues,
			"projection.tool.values must be present and non-empty for an enum tool projection")
		projectedTools := strings.Split(toolValues, ",")
		for _, want := range toolProjectionValues {
			assert.Contains(t, projectedTools, want,
				"projection.tool.values %q must enumerate the configured tool value %q (the governed defaults plus this run's unique tool)", toolValues, want)
		}

		assert.Equal(t, "date", params["projection.dt.type"],
			"dt must be a date projection dimension")
		assert.Equal(t, "yyyy-MM-dd", params["projection.dt.format"],
			"projection.dt.format must equal the Firehose !{timestamp:yyyy-MM-dd} partition format")
	})

	// BUG-3: the storage.location.template must resolve to the exact S3 path Firehose
	// writes -- s3://<bucket>/raw/tool=${tool}/dt=${dt}/ -- so each projected partition
	// maps onto the delivered objects. A mismatch makes queries return nothing. The
	// expected template is derived from the actually-created lake bucket ARN so the
	// assertion stays input-driven.
	t.Run("GlueTableStorageLocationTemplateMatchesFirehosePath", func(t *testing.T) {
		bucketArn := terraform.Output(t, ctx.Terraform, "data_lake_bucket_arn")
		require.NotEmpty(t, bucketArn, "data_lake_bucket_arn must be present")
		bucketName := strings.TrimPrefix(bucketArn, "arn:aws:s3:::")
		require.NotEqual(t, bucketArn, bucketName, "data_lake_bucket_arn must be an S3 bucket ARN")

		params := terraform.OutputMap(t, ctx.Terraform, "glue_table_parameters")
		expectedTemplate := fmt.Sprintf("s3://%s/raw/tool=${tool}/dt=${dt}/", bucketName)
		assert.Equal(t, expectedTemplate, params["storage.location.template"],
			"storage.location.template must map the ${tool}/${dt} placeholders onto the Firehose raw/ delivery path rooted at the lake bucket")
	})

	// feat(data-lake) cross-account read grant -- DEFAULT OFF. With
	// cross_account_read_principals unset ({}), the grant is a no-op: NO
	// aws_s3_bucket_policy resource is created and the cross_account_kms_statements
	// output is an empty list, so existing behavior is preserved. This proves the
	// capability is default-off (the actual ARN is wired later in the terragrunt leaf).
	t.Run("CrossAccountReadGrantDefaultOff", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_policy", 0)

		kmsStatements := terraform.OutputListOfObjects(t, ctx.Terraform, "cross_account_kms_statements")
		assert.Empty(t, kmsStatements,
			"cross_account_kms_statements must be an empty list when no cross-account principals are configured")

		accountIDs := terraform.OutputList(t, ctx.Terraform, "cross_account_principal_account_ids")
		assert.Empty(t, accountIDs,
			"no cross-account principals must be configured in the default (no-grant) fixture")
	})

	// feat(data-lake) perf-provisioning: the default fixture applies with the module's default
	// cwl_transform_lambda_memory_size (512 MB, bumped from the prior 256 MB baseline). The
	// transform Lambda re-ingests each logEvents[].message as its own PutRecordBatch record, so a
	// single invocation holds the whole decompressed CloudWatch Logs delivery in memory; this
	// pins the applied Lambda's memory_size to the new default so a future regression (e.g. an
	// accidental revert to 256) is caught here instead of only surfacing as throttling/OOM under
	// high-volume ingestion.
	t.Run("CwlTransformLambdaMemorySizeMatchesDefault", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_lambda_function", 1)

		lambdaValues, found := findLambdaFunctionValues(t, ctx)
		require.True(t, found, "the applied state must contain an aws_lambda_function resource")

		memorySize, ok := lambdaValues["memory_size"].(float64)
		require.True(t, ok, "aws_lambda_function memory_size must be a number in state")
		assert.Equal(t, float64(512), memorySize,
			"cwl_split transform Lambda memory_size must equal the module's default (512 MB), sized for the re-ingestion workload")
	})

	// Assert the pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})
}

// TestDataLakeCwlTransformLambdaReservedConcurrency applies the default fixture with
// cwl_transform_lambda_reserved_concurrent_executions set to a small, explicit non-zero value
// (feat data-lake perf-provisioning input) and asserts the applied Lambda's
// reserved_concurrent_executions equals that value -- proving the input is actually wired onto
// the aws_lambda_function resource, not merely accepted and ignored. A distinct base name
// isolates every derived resource name from the default (unreserved) fixture so the two applies
// never collide on a globally/account-unique name.
func TestDataLakeCwlTransformLambdaReservedConcurrency(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	extraVars := mustTaggingVars(t)
	const reservedConcurrency = 5
	extraVars["cwl_transform_lambda_reserved_concurrent_executions"] = reservedConcurrency
	extraVars["name"] = "tt-dl-cwlrc"

	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("data-lake-cwlrc-%s", suffix),
		ExtraVars: extraVars,
	})

	t.Run("ReservedConcurrentExecutionsApplied", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_lambda_function", 1)

		lambdaValues, found := findLambdaFunctionValues(t, ctx)
		require.True(t, found, "the applied state must contain an aws_lambda_function resource")

		reserved, ok := lambdaValues["reserved_concurrent_executions"].(float64)
		require.True(t, ok, "aws_lambda_function reserved_concurrent_executions must be a number in state")
		assert.Equal(t, float64(reservedConcurrency), reserved,
			"cwl_transform_lambda_reserved_concurrent_executions must be wired onto the Lambda's reserved_concurrent_executions so the Firehose transform's concurrency is guaranteed from the account pool")
	})
}

// TestDataLakeInvalidTransitionStorageClass asserts that an invalid transition_storage_class
// causes a plan validation error (error path coverage).
func TestDataLakeInvalidTransitionStorageClass(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/default", testctx.TestConfig{
		Name: fmt.Sprintf("data-lake-bad-storage-class-%s", suffix),
		ExtraVars: map[string]interface{}{
			"transition_storage_class": "INVALID_CLASS",
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when transition_storage_class is not a valid storage class")
}

// TestDataLakeCrossAccountReadGrant applies the fixture with the cross-account read grant
// ENABLED (feat data-lake). enable_cross_account_read_fixture grants the CURRENT test
// account root -- a real, PutBucketPolicy-valid principal that exercises the exact
// cross-account grant shape (account root, the correct target for an external role that
// assumes within its own account). It asserts:
//   - exactly one aws_s3_bucket_policy is created (count 1);
//   - the applied bucket policy grants the account root s3:GetObject + s3:ListBucket scoped
//     to the lake bucket (never wildcard);
//   - the cross_account_kms_statements output carries the matching kms:Decrypt +
//     kms:DescribeKey statement (Principal = account root, kms:ViaService = s3.<region>...)
//     that the terragrunt leaf merges into the telemetry-data CMK key policy.
//
// A distinct base name isolates every derived resource name from the default fixture so the
// two applies (default OFF, this one ON) never collide on a globally/account-unique name.
func TestDataLakeCrossAccountReadGrant(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	extraVars := mustTaggingVars(t)
	extraVars["enable_cross_account_read_fixture"] = true
	extraVars["name"] = "tt-dl-xacct"

	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name:      fmt.Sprintf("data-lake-xacct-%s", suffix),
		ExtraVars: extraVars,
	})

	accountIDs := terraform.OutputList(t, ctx.Terraform, "cross_account_principal_account_ids")
	require.Len(t, accountIDs, 1, "the enabled fixture must configure exactly one cross-account principal")
	expectedPrincipal := fmt.Sprintf("arn:aws:iam::%s:root", accountIDs[0])

	// Exactly one bucket policy: the cross-account read grant. The lake and access-log
	// s3-bucket primitives leave their optional policy resource at count 0.
	t.Run("BucketPolicyCreated", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_s3_bucket_policy", 1)
	})

	// The APPLIED bucket policy (read from state) grants the account root S3 read scoped
	// to the lake bucket -- never wildcard, per docs/terragrunt-concepts.md scoping.
	t.Run("BucketPolicyGrantsCrossAccountS3Read", func(t *testing.T) {
		bucketArn := terraform.Output(t, ctx.Terraform, "data_lake_bucket_arn")
		require.NotEmpty(t, bucketArn, "data_lake_bucket_arn must be present")

		policyJSON := findAppliedBucketPolicy(t, ctx)
		require.NotEmpty(t, policyJSON, "the applied aws_s3_bucket_policy must carry a policy document")

		var policy map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(policyJSON), &policy),
			"the applied bucket policy must be valid JSON")
		statements, ok := policy["Statement"].([]interface{})
		require.True(t, ok, "bucket policy Statement must be an array")

		foundS3 := false
		for _, rawStmt := range statements {
			stmt, ok := rawStmt.(map[string]interface{})
			if !ok {
				continue
			}
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "s3:GetObject", "s3:ListBucket") {
				continue
			}
			foundS3 = true
			assert.Contains(t, actions, "s3:GetObject", "cross-account S3 statement must grant s3:GetObject")
			assert.Contains(t, actions, "s3:ListBucket", "cross-account S3 statement must grant s3:ListBucket")

			principal, ok := stmt["Principal"].(map[string]interface{})
			require.True(t, ok, "cross-account S3 statement Principal must be an object")
			principals := extractStringSlice(principal["AWS"])
			assert.Contains(t, principals, expectedPrincipal,
				"cross-account S3 statement must grant the configured account root %s", expectedPrincipal)

			resources := extractStringSlice(stmt["Resource"])
			assert.NotEmpty(t, resources, "cross-account S3 statement must scope Resource")
			for _, r := range resources {
				assert.NotEqual(t, "*", r, "cross-account S3 Resource must NOT be wildcard *")
				assert.True(t, strings.HasPrefix(r, bucketArn),
					"cross-account S3 Resource %q must be scoped to the lake bucket %q", r, bucketArn)
			}
		}
		assert.True(t, foundS3, "the applied bucket policy must contain the cross-account S3 read statement")
	})

	// The cross_account_kms_statements output carries the matching Decrypt/DescribeKey grant
	// (Principal = account root, kms:ViaService = s3.<region>.amazonaws.com) for the leaf to
	// merge into the telemetry-data key policy it owns.
	t.Run("KmsStatementsOutputCarriesDecryptGrant", func(t *testing.T) {
		region := terraform.Output(t, ctx.Terraform, "aws_region")
		require.NotEmpty(t, region, "aws_region output required for the ViaService assertion")
		expectedViaService := fmt.Sprintf("s3.%s.amazonaws.com", region)

		statements := terraform.OutputListOfObjects(t, ctx.Terraform, "cross_account_kms_statements")
		require.NotEmpty(t, statements, "cross_account_kms_statements must be non-empty when the grant is enabled")

		foundKms := false
		for _, stmt := range statements {
			actions := extractStringSlice(stmt["Action"])
			if !containsAny(actions, "kms:Decrypt", "kms:DescribeKey") {
				continue
			}
			foundKms = true
			assert.Contains(t, actions, "kms:Decrypt", "KMS statement must grant kms:Decrypt")
			assert.Contains(t, actions, "kms:DescribeKey", "KMS statement must grant kms:DescribeKey")

			principal, ok := stmt["Principal"].(map[string]interface{})
			require.True(t, ok, "KMS statement Principal must be an object")
			principals := extractStringSlice(principal["AWS"])
			assert.Contains(t, principals, expectedPrincipal,
				"KMS statement must grant the configured account root %s", expectedPrincipal)

			condition, ok := stmt["Condition"].(map[string]interface{})
			require.True(t, ok, "KMS statement must carry a Condition block")
			stringEquals, ok := condition["StringEquals"].(map[string]interface{})
			require.True(t, ok, "KMS statement Condition must use StringEquals")
			viaService, ok := stringEquals["kms:ViaService"].(string)
			require.True(t, ok, "KMS statement Condition must key on kms:ViaService")
			assert.Equal(t, expectedViaService, viaService,
				"KMS statement kms:ViaService must be %s", expectedViaService)
		}
		assert.True(t, foundKms, "cross_account_kms_statements must contain the Decrypt/DescribeKey statement")
	})
}

// getFirehoseRolePolicyStatements fetches the Firehose delivery role inline policy JSON
// from the Terraform output, unmarshals it, and returns the parsed Statement array.
// All per-statement subtests share this helper to avoid duplicating the
// fetch-unmarshal-extract block (DRY).
func getFirehoseRolePolicyStatements(t *testing.T, ctx testctx.TestContext) []interface{} {
	t.Helper()

	firehoseRolePolicyJSON := terraform.Output(t, ctx.Terraform, "firehose_role_inline_policy_json")
	require.NotEmpty(t, firehoseRolePolicyJSON, "firehose_role_inline_policy_json must be exposed for policy assertion")

	var policy map[string]interface{}
	err := json.Unmarshal([]byte(firehoseRolePolicyJSON), &policy)
	require.NoError(t, err, "firehose_role_inline_policy_json must be valid JSON")

	statements, ok := policy["Statement"].([]interface{})
	require.True(t, ok, "policy Statement must be an array")

	return statements
}

// findSchemaConfigurationRoleArn loads the applied state via terraform show -json,
// walks the module tree to the aws_kinesis_firehose_delivery_stream resource, and
// returns the role_arn nested under
// extended_s3_configuration[].data_format_conversion_configuration[].schema_configuration[].
// Returns "" when no schema_configuration role_arn is present.
func findSchemaConfigurationRoleArn(t *testing.T, ctx testctx.TestContext) string {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect schema_configuration role_arn")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values, ok := state["values"].(map[string]interface{})
	require.True(t, ok, "state JSON must contain a 'values' object")

	rootModule, ok := values["root_module"].(map[string]interface{})
	require.True(t, ok, "state values must contain a 'root_module' object")

	return walkForSchemaRoleArn(rootModule)
}

// walkForSchemaRoleArn recursively searches a state module tree for the first
// aws_kinesis_firehose_delivery_stream resource and extracts the schema_configuration
// role_arn from its extended_s3_configuration block.
func walkForSchemaRoleArn(module map[string]interface{}) string {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_kinesis_firehose_delivery_stream" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			extConfigs, ok := vals["extended_s3_configuration"].([]interface{})
			if !ok {
				continue
			}
			for _, rawExt := range extConfigs {
				ext, ok := rawExt.(map[string]interface{})
				if !ok {
					continue
				}
				dfcConfigs, ok := ext["data_format_conversion_configuration"].([]interface{})
				if !ok {
					continue
				}
				for _, rawDfc := range dfcConfigs {
					dfc, ok := rawDfc.(map[string]interface{})
					if !ok {
						continue
					}
					schemaConfigs, ok := dfc["schema_configuration"].([]interface{})
					if !ok {
						continue
					}
					for _, rawSchema := range schemaConfigs {
						schema, ok := rawSchema.(map[string]interface{})
						if !ok {
							continue
						}
						if roleArn, ok := schema["role_arn"].(string); ok && roleArn != "" {
							return roleArn
						}
					}
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if found := walkForSchemaRoleArn(child); found != "" {
				return found
			}
		}
	}

	return ""
}

// findFirehoseProcessorTypes loads the applied state, walks to the first
// aws_kinesis_firehose_delivery_stream, and returns the ordered list of processor
// "type" values from extended_s3_configuration[].processing_configuration[].processors.
func findFirehoseProcessorTypes(t *testing.T, ctx testctx.TestContext) []string {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect firehose processors")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values := state["values"].(map[string]interface{})
	rootModule := values["root_module"].(map[string]interface{})
	return walkForProcessorTypes(rootModule)
}

// walkForProcessorTypes recursively searches a state module tree for the first
// aws_kinesis_firehose_delivery_stream resource and returns its ordered processor types.
func walkForProcessorTypes(module map[string]interface{}) []string {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_kinesis_firehose_delivery_stream" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			extConfigs, ok := vals["extended_s3_configuration"].([]interface{})
			if !ok || len(extConfigs) == 0 {
				continue
			}
			ext := extConfigs[0].(map[string]interface{})
			procConfigs, ok := ext["processing_configuration"].([]interface{})
			if !ok || len(procConfigs) == 0 {
				continue
			}
			procConfig := procConfigs[0].(map[string]interface{})
			processors, ok := procConfig["processors"].([]interface{})
			if !ok {
				continue
			}
			types := make([]string, 0, len(processors))
			for _, pRaw := range processors {
				p := pRaw.(map[string]interface{})
				if pt, ok := p["type"].(string); ok {
					types = append(types, pt)
				}
			}
			return types
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if found := walkForProcessorTypes(child); found != nil {
				return found
			}
		}
	}

	return nil
}

// findFirehoseMetadataExtractionQuery loads the applied state, walks to the first
// aws_kinesis_firehose_delivery_stream, and returns the MetadataExtractionQuery
// parameter value from its MetadataExtraction processor. This is the exact JQ string
// Firehose compiles for dynamic partitioning; a malformed value (e.g. a strftime token)
// makes the query fail to compile and routes every record to errors/ (BUG-4).
func findFirehoseMetadataExtractionQuery(t *testing.T, ctx testctx.TestContext) string {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect MetadataExtractionQuery")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values := state["values"].(map[string]interface{})
	rootModule := values["root_module"].(map[string]interface{})
	return walkForMetadataExtractionQuery(rootModule)
}

// walkForMetadataExtractionQuery recursively searches a state module tree for the first
// aws_kinesis_firehose_delivery_stream and returns the MetadataExtractionQuery parameter
// value of its MetadataExtraction processor (empty string when not found).
func walkForMetadataExtractionQuery(module map[string]interface{}) string {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_kinesis_firehose_delivery_stream" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			extConfigs, ok := vals["extended_s3_configuration"].([]interface{})
			if !ok || len(extConfigs) == 0 {
				continue
			}
			ext := extConfigs[0].(map[string]interface{})
			procConfigs, ok := ext["processing_configuration"].([]interface{})
			if !ok || len(procConfigs) == 0 {
				continue
			}
			procConfig := procConfigs[0].(map[string]interface{})
			processors, ok := procConfig["processors"].([]interface{})
			if !ok {
				continue
			}
			for _, pRaw := range processors {
				p, ok := pRaw.(map[string]interface{})
				if !ok {
					continue
				}
				if pt, _ := p["type"].(string); pt != "MetadataExtraction" {
					continue
				}
				params, ok := p["parameters"].([]interface{})
				if !ok {
					continue
				}
				for _, paramRaw := range params {
					param, ok := paramRaw.(map[string]interface{})
					if !ok {
						continue
					}
					if name, _ := param["parameter_name"].(string); name == "MetadataExtractionQuery" {
						if value, ok := param["parameter_value"].(string); ok {
							return value
						}
					}
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if found := walkForMetadataExtractionQuery(child); found != "" {
				return found
			}
		}
	}

	return ""
}

// findKmsKeyRotationEnabled walks the Terraform state module tree and returns
// the enable_key_rotation attribute value of the first aws_kms_key resource found.
func findKmsKeyRotationEnabled(t *testing.T, module map[string]interface{}) bool {
	t.Helper()

	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType == "aws_kms_key" {
				values, ok := res["values"].(map[string]interface{})
				if !ok {
					continue
				}
				if rotation, ok := values["enable_key_rotation"].(bool); ok {
					return rotation
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if found := findKmsKeyRotationEnabled(t, child); found {
				return true
			}
		}
	}

	return false
}

// findLambdaFunctionValues loads the applied state via terraform show -json, walks the module
// tree, and returns the "values" map of the first aws_lambda_function resource found -- the
// CloudWatch-Logs-split transform Lambda (the module composes exactly one, asserted by
// FirehoseTransformLambdaProcessors above). Returns nil, false when none is found. Used by the
// memory_size and reserved_concurrent_executions sizing assertions.
func findLambdaFunctionValues(t *testing.T, ctx testctx.TestContext) (map[string]interface{}, bool) {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect the Lambda function")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values, ok := state["values"].(map[string]interface{})
	require.True(t, ok, "state JSON must contain a 'values' object")

	rootModule, ok := values["root_module"].(map[string]interface{})
	require.True(t, ok, "state values must contain a 'root_module' object")

	return walkForLambdaFunctionValues(rootModule)
}

// walkForLambdaFunctionValues recursively searches a state module tree for the first
// aws_lambda_function resource and returns its values map and whether one was found.
func walkForLambdaFunctionValues(module map[string]interface{}) (map[string]interface{}, bool) {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_lambda_function" {
				continue
			}
			if vals, ok := res["values"].(map[string]interface{}); ok {
				return vals, true
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if vals, found := walkForLambdaFunctionValues(child); found {
				return vals, true
			}
		}
	}

	return nil, false
}

// findGlueTableColumnsAndPartitionKeys loads the applied state via terraform
// show -json, walks the module tree to the first aws_glue_catalog_table, and
// returns its data-column names (storage_descriptor[].columns[].name) and its
// partition-key names (partition_keys[].name). Used by the duplicate-column guard.
func findGlueTableColumnsAndPartitionKeys(t *testing.T, ctx testctx.TestContext) ([]string, []string) {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect the Glue table descriptor")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values, ok := state["values"].(map[string]interface{})
	require.True(t, ok, "state JSON must contain a 'values' object")

	rootModule, ok := values["root_module"].(map[string]interface{})
	require.True(t, ok, "state values must contain a 'root_module' object")

	columns, partitionKeys, found := walkForGlueTableColumns(rootModule)
	require.True(t, found, "the applied state must contain an aws_glue_catalog_table resource")
	return columns, partitionKeys
}

// walkForGlueTableColumns recursively searches a state module tree for the first
// aws_glue_catalog_table resource and returns its data-column names, its
// partition-key names, and whether a table was found.
func walkForGlueTableColumns(module map[string]interface{}) ([]string, []string, bool) {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_glue_catalog_table" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}

			columns := make([]string, 0)
			if sds, ok := vals["storage_descriptor"].([]interface{}); ok {
				for _, rawSD := range sds {
					sd, ok := rawSD.(map[string]interface{})
					if !ok {
						continue
					}
					cols, ok := sd["columns"].([]interface{})
					if !ok {
						continue
					}
					for _, rawCol := range cols {
						col, ok := rawCol.(map[string]interface{})
						if !ok {
							continue
						}
						if name, ok := col["name"].(string); ok {
							columns = append(columns, name)
						}
					}
				}
			}

			partitionKeys := make([]string, 0)
			if pks, ok := vals["partition_keys"].([]interface{}); ok {
				for _, rawPK := range pks {
					pk, ok := rawPK.(map[string]interface{})
					if !ok {
						continue
					}
					if name, ok := pk["name"].(string); ok {
						partitionKeys = append(partitionKeys, name)
					}
				}
			}

			return columns, partitionKeys, true
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if columns, partitionKeys, found := walkForGlueTableColumns(child); found {
				return columns, partitionKeys, true
			}
		}
	}

	return nil, nil, false
}

// findAppliedBucketPolicy loads the applied state via terraform show -json, walks the module
// tree to the first aws_s3_bucket_policy resource, and returns its policy attribute JSON
// (empty string when none is present). Used by the cross-account read-grant assertions.
func findAppliedBucketPolicy(t *testing.T, ctx testctx.TestContext) string {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect the bucket policy")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state),
		"terraform show -json output must be valid JSON")

	values, ok := state["values"].(map[string]interface{})
	require.True(t, ok, "state JSON must contain a 'values' object")

	rootModule, ok := values["root_module"].(map[string]interface{})
	require.True(t, ok, "state values must contain a 'root_module' object")

	return walkForBucketPolicy(rootModule)
}

// walkForBucketPolicy recursively searches a state module tree for the first
// aws_s3_bucket_policy resource and returns its policy attribute (empty string when none).
func walkForBucketPolicy(module map[string]interface{}) string {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_s3_bucket_policy" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			if policy, ok := vals["policy"].(string); ok && policy != "" {
				return policy
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if found := walkForBucketPolicy(child); found != "" {
				return found
			}
		}
	}

	return ""
}

// extractStringSlice converts an interface{} that may be a string or []interface{} into []string.
func extractStringSlice(v interface{}) []string {
	switch val := v.(type) {
	case string:
		return []string{val}
	case []interface{}:
		result := make([]string, 0, len(val))
		for _, item := range val {
			if s, ok := item.(string); ok {
				result = append(result, s)
			}
		}
		return result
	}
	return nil
}

// containsAny returns true when at least one needle appears in haystack.
func containsAny(haystack []string, needles ...string) bool {
	for _, h := range haystack {
		for _, n := range needles {
			if h == n {
				return true
			}
		}
	}
	return false
}

// countS3Objects returns the number of objects under the given prefix.
func countS3Objects(t *testing.T, ctxBg context.Context, s3Client *s3.Client, bucket, prefix string) int {
	t.Helper()
	count := 0
	var token *string
	for {
		out, err := s3Client.ListObjectsV2(ctxBg, &s3.ListObjectsV2Input{
			Bucket:            aws.String(bucket),
			Prefix:            aws.String(prefix),
			ContinuationToken: token,
		})
		require.NoError(t, err, "listing s3://%s/%s must succeed", bucket, prefix)
		count += len(out.Contents)
		if out.IsTruncated == nil || !*out.IsTruncated {
			break
		}
		token = out.NextContinuationToken
	}
	return count
}

// listS3Keys returns the object keys under the given prefix.
func listS3Keys(t *testing.T, ctxBg context.Context, s3Client *s3.Client, bucket, prefix string) []string {
	t.Helper()
	keys := []string{}
	out, err := s3Client.ListObjectsV2(ctxBg, &s3.ListObjectsV2Input{
		Bucket: aws.String(bucket),
		Prefix: aws.String(prefix),
	})
	require.NoError(t, err, "listing s3://%s/%s must succeed", bucket, prefix)
	for _, obj := range out.Contents {
		if obj.Key != nil {
			keys = append(keys, *obj.Key)
		}
	}
	return keys
}

// newAthenaResultsBucket creates a dedicated, plain (SSE-S3) bucket for Athena query results
// and registers a t.Cleanup that empties and deletes it. Keeping results out of the
// KMS-encrypted data lake bucket avoids needing the query identity to write through the CMK
// and keeps the lake free of non-telemetry objects. Returns the s3:// results location.
func newAthenaResultsBucket(t *testing.T, ctxBg context.Context, s3Client *s3.Client, region string) string {
	t.Helper()
	bucket := fmt.Sprintf("tt-dl-athena-%d", time.Now().UnixNano())

	input := &s3.CreateBucketInput{Bucket: aws.String(bucket)}
	// us-east-1 is the API default and rejects an explicit LocationConstraint.
	if region != "us-east-1" {
		input.CreateBucketConfiguration = &s3types.CreateBucketConfiguration{
			LocationConstraint: s3types.BucketLocationConstraint(region),
		}
	}
	_, err := s3Client.CreateBucket(ctxBg, input)
	require.NoError(t, err, "creating the Athena results bucket must succeed")

	t.Cleanup(func() {
		var token *string
		for {
			out, listErr := s3Client.ListObjectsV2(ctxBg, &s3.ListObjectsV2Input{
				Bucket: aws.String(bucket), ContinuationToken: token,
			})
			if listErr != nil {
				break
			}
			for _, obj := range out.Contents {
				_, _ = s3Client.DeleteObject(ctxBg, &s3.DeleteObjectInput{Bucket: aws.String(bucket), Key: obj.Key})
			}
			if out.IsTruncated == nil || !*out.IsTruncated {
				break
			}
			token = out.NextContinuationToken
		}
		_, _ = s3Client.DeleteBucket(ctxBg, &s3.DeleteBucketInput{Bucket: aws.String(bucket)})
	})

	return fmt.Sprintf("s3://%s/results/", bucket)
}

// runAthenaQuery executes a query against the given Glue database, polls (bounded) for it to
// reach a terminal state, and returns the data rows (header row excluded). A NULL cell is
// returned as the empty string so callers can assert non-NULL behavior. Fails the test on a
// FAILED/CANCELLED query or on poll-timeout.
func runAthenaQuery(t *testing.T, ctxBg context.Context, client *athena.Client, database, outputLocation, query string) [][]string {
	t.Helper()

	start, err := client.StartQueryExecution(ctxBg, &athena.StartQueryExecutionInput{
		QueryString:           aws.String(query),
		QueryExecutionContext: &athenatypes.QueryExecutionContext{Database: aws.String(database)},
		ResultConfiguration:   &athenatypes.ResultConfiguration{OutputLocation: aws.String(outputLocation)},
	})
	require.NoError(t, err, "StartQueryExecution must succeed for query: %s", query)
	queryID := aws.ToString(start.QueryExecutionId)

	_, pollErr := retry.DoWithRetryE(
		t,
		fmt.Sprintf("await Athena query %s", queryID),
		60, 2*time.Second,
		func() (string, error) {
			exec, execErr := client.GetQueryExecution(ctxBg, &athena.GetQueryExecutionInput{
				QueryExecutionId: aws.String(queryID),
			})
			if execErr != nil {
				return "", execErr
			}
			switch exec.QueryExecution.Status.State {
			case athenatypes.QueryExecutionStateSucceeded:
				return "succeeded", nil
			case athenatypes.QueryExecutionStateFailed, athenatypes.QueryExecutionStateCancelled:
				reason := aws.ToString(exec.QueryExecution.Status.StateChangeReason)
				return "", retry.FatalError{Underlying: fmt.Errorf("Athena query %s %s: %s", queryID, exec.QueryExecution.Status.State, reason)}
			default:
				return "", fmt.Errorf("Athena query %s still running", queryID)
			}
		},
	)
	require.NoError(t, pollErr, "Athena query must reach SUCCEEDED")

	results, err := client.GetQueryResults(ctxBg, &athena.GetQueryResultsInput{
		QueryExecutionId: aws.String(queryID),
	})
	require.NoError(t, err, "GetQueryResults must succeed")
	require.NotNil(t, results.ResultSet, "Athena result set must be present")

	rows := make([][]string, 0, len(results.ResultSet.Rows))
	for i, row := range results.ResultSet.Rows {
		if i == 0 {
			// Skip the column-header row.
			continue
		}
		cells := make([]string, 0, len(row.Data))
		for _, datum := range row.Data {
			cells = append(cells, aws.ToString(datum.VarCharValue))
		}
		rows = append(rows, cells)
	}
	return rows
}

// findFirehoseProcessorParameter loads the applied state, walks to the first
// aws_kinesis_firehose_delivery_stream, and returns the parameter value of the named
// parameter on the processor of the given type (empty string when not found).
func findFirehoseProcessorParameter(t *testing.T, ctx testctx.TestContext, processorType, parameterName string) string {
	t.Helper()

	stateJSON, err := terraform.RunTerraformCommandAndGetStdoutE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must succeed to inspect firehose processor parameters")
	require.NotEmpty(t, stateJSON, "terraform show -json must return non-empty state JSON")

	var state map[string]interface{}
	require.NoError(t, json.Unmarshal([]byte(stateJSON), &state), "terraform show -json output must be valid JSON")

	values := state["values"].(map[string]interface{})
	rootModule := values["root_module"].(map[string]interface{})
	return walkForProcessorParameter(rootModule, processorType, parameterName)
}

// walkForProcessorParameter recursively searches a state module tree for the first
// aws_kinesis_firehose_delivery_stream and returns the named parameter value of the processor
// with the given type.
func walkForProcessorParameter(module map[string]interface{}, processorType, parameterName string) string {
	if resources, ok := module["resources"].([]interface{}); ok {
		for _, rawRes := range resources {
			res, ok := rawRes.(map[string]interface{})
			if !ok {
				continue
			}
			if resType, _ := res["type"].(string); resType != "aws_kinesis_firehose_delivery_stream" {
				continue
			}
			vals, ok := res["values"].(map[string]interface{})
			if !ok {
				continue
			}
			extConfigs, ok := vals["extended_s3_configuration"].([]interface{})
			if !ok || len(extConfigs) == 0 {
				continue
			}
			ext := extConfigs[0].(map[string]interface{})
			procConfigs, ok := ext["processing_configuration"].([]interface{})
			if !ok || len(procConfigs) == 0 {
				continue
			}
			procConfig := procConfigs[0].(map[string]interface{})
			processors, ok := procConfig["processors"].([]interface{})
			if !ok {
				continue
			}
			for _, pRaw := range processors {
				p, ok := pRaw.(map[string]interface{})
				if !ok {
					continue
				}
				if pt, _ := p["type"].(string); pt != processorType {
					continue
				}
				params, ok := p["parameters"].([]interface{})
				if !ok {
					continue
				}
				for _, paramRaw := range params {
					param, ok := paramRaw.(map[string]interface{})
					if !ok {
						continue
					}
					if name, _ := param["parameter_name"].(string); name == parameterName {
						if value, ok := param["parameter_value"].(string); ok {
							return value
						}
					}
				}
			}
		}
	}

	if childModules, ok := module["child_modules"].([]interface{}); ok {
		for _, rawChild := range childModules {
			child, ok := rawChild.(map[string]interface{})
			if !ok {
				continue
			}
			if found := walkForProcessorParameter(child, processorType, parameterName); found != "" {
				return found
			}
		}
	}

	return ""
}

//go:build terratest

package basic_test

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/route53"
	route53types "github.com/aws/aws-sdk-go-v2/service/route53/types"
	terratest_aws "github.com/gruntwork-io/terratest/modules/aws"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// testZoneBaseName is the base DNS suffix used to construct a unique, ephemeral
// public hosted zone for each live record test. A per-test unique label is
// prepended so concurrent or repeated runs never collide.
const testZoneBaseName = "example-terratest.net"

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

// route53Region is the region placeholder for Route53 API calls. Route53 is a
// global service and the API ignores the region, but the SDK client still
// requires one.
const route53Region = "us-east-1"

// hostedZoneFixture provisions a self-contained, ephemeral public Route53 hosted
// zone in the target account, registers t.Cleanup to scrub non-default records
// and delete the zone after the test completes, and returns the zone ID and the
// zone name (without the trailing dot).
//
// Because Go t.Cleanup is LIFO and this fixture is invoked before
// RunSingleExample, the zone is deleted only after the terraform Destroy
// registered by RunSingleExample has removed the record this test created.
//
// The zone is created with a per-test unique label so repeated or concurrent
// runs never collide, making the suite runnable in any clean account with no
// pre-provisioned fixture.
func hostedZoneFixture(t *testing.T) (string, string) {
	t.Helper()
	client := terratest_aws.NewRoute53Client(t, route53Region)
	// UnixNano guarantees a unique zone name even when two tests start within the
	// same second, so concurrent or repeated runs never collide on the zone apex.
	unique := fmt.Sprintf("%d", time.Now().UnixNano())
	zoneName := fmt.Sprintf("%s-%s.%s", "tt", unique, testZoneBaseName)

	createOut, err := client.CreateHostedZone(context.Background(), &route53.CreateHostedZoneInput{
		Name:            aws.String(zoneName),
		CallerReference: aws.String(fmt.Sprintf("terratest-%s", unique)),
		HostedZoneConfig: &route53types.HostedZoneConfig{
			Comment:     aws.String("Ephemeral terratest hosted zone; safe to delete."),
			PrivateZone: false,
		},
	})
	require.NoError(t, err, "CreateHostedZone must succeed for the route53-record test fixture")

	// Zone Id is returned as /hostedzone/<id> -- strip the prefix.
	zoneID := aws.ToString(createOut.HostedZone.Id)
	if idx := strings.LastIndex(zoneID, "/"); idx >= 0 {
		zoneID = zoneID[idx+1:]
	}

	t.Cleanup(func() {
		scrubHostedZoneRecords(t, client, zoneID)
		_, deleteErr := client.DeleteHostedZone(context.Background(), &route53.DeleteHostedZoneInput{
			Id: aws.String(zoneID),
		})
		assert.NoError(t, deleteErr, "DeleteHostedZone must succeed during fixture cleanup for zone %s", zoneID)
	})

	return zoneID, strings.TrimSuffix(zoneName, ".")
}

// scrubHostedZoneRecords deletes every resource record set in the zone except the
// apex NS and SOA records, which Route53 requires to remain until the zone itself
// is deleted. This is defensive cleanup: under normal flow terraform Destroy has
// already removed the test record, but if apply failed mid-run this guarantees the
// zone is empty enough to delete so no orphan is left behind.
func scrubHostedZoneRecords(t *testing.T, client *route53.Client, zoneID string) {
	t.Helper()
	listOut, err := client.ListResourceRecordSets(context.Background(), &route53.ListResourceRecordSetsInput{
		HostedZoneId: aws.String(zoneID),
	})
	if !assert.NoError(t, err, "ListResourceRecordSets must succeed during fixture cleanup for zone %s", zoneID) {
		return
	}

	var changes []route53types.Change
	for i := range listOut.ResourceRecordSets {
		// Take the address of the slice element directly; a loop-local copy would
		// alias across iterations and make every Change point at the same record.
		rrset := &listOut.ResourceRecordSets[i]
		if rrset.Type == route53types.RRTypeNs || rrset.Type == route53types.RRTypeSoa {
			continue
		}
		changes = append(changes, route53types.Change{
			Action:            route53types.ChangeActionDelete,
			ResourceRecordSet: rrset,
		})
	}
	if len(changes) == 0 {
		return
	}

	_, changeErr := client.ChangeResourceRecordSets(context.Background(), &route53.ChangeResourceRecordSetsInput{
		HostedZoneId: aws.String(zoneID),
		ChangeBatch: &route53types.ChangeBatch{
			Changes: changes,
			Comment: aws.String("terratest fixture cleanup: remove residual records before zone deletion"),
		},
	})
	assert.NoError(t, changeErr, "ChangeResourceRecordSets (delete residual records) must succeed during fixture cleanup for zone %s", zoneID)
}

// testRecordName returns a unique record name within the given ephemeral hosted
// zone, producing a name that is safe to create as a CNAME record.
func testRecordName(prefix, suffix, zoneName string) string {
	return fmt.Sprintf("%s-%s.%s", prefix, suffix, zoneName)
}

// TestBasicRoute53RecordRequiredOutputsNotEmpty asserts all required outputs are non-empty.
func TestBasicRoute53RecordRequiredOutputsNotEmpty(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	zoneID, zoneName := hostedZoneFixture(t)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-r53-outputs-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"zone_id": zoneID,
			"name":    testRecordName("test-out", suffix, zoneName),
			"records": []string{fmt.Sprintf("_val-%s.acm-validations.aws.", suffix)},
		}, mustTaggingVars(t)),
	})

	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "fqdn"), "fqdn must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "name"), "name must not be empty")
	assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "record_type"), "record_type must not be empty")
}

// TestBasicRoute53RecordResourceCount asserts exactly one aws_route53_record is created.
func TestBasicRoute53RecordResourceCount(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	zoneID, zoneName := hostedZoneFixture(t)
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-r53-count-%s", suffix),
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"zone_id": zoneID,
			"name":    testRecordName("test-cnt", suffix, zoneName),
			"records": []string{fmt.Sprintf("_val-%s.acm-validations.aws.", suffix)},
		}, mustTaggingVars(t)),
	})

	assertions.AssertResourceCount(t, ctx, "aws_route53_record", 1)
}

// TestBasicRoute53RecordInvalidType asserts that an invalid record type fails validation.
func TestBasicRoute53RecordInvalidType(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-r53-bad-type-%s", suffix),
		ExtraVars: map[string]interface{}{
			"zone_id": "Z1234567890ABCDEFGHIJ",
			"name":    fmt.Sprintf("_test-%s.example.com", suffix),
			"type":    "INVALID",
			"records": []string{"somevalue"},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when type is not a valid Route53 record type")
}

// TestBasicRoute53RecordEmptyRecords asserts that an empty records list fails validation.
func TestBasicRoute53RecordEmptyRecords(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	opts := testctx.InitTerraform("../../examples/basic", testctx.TestConfig{
		Name: fmt.Sprintf("basic-r53-empty-records-%s", suffix),
		ExtraVars: map[string]interface{}{
			"zone_id": "Z1234567890ABCDEFGHIJ",
			"name":    fmt.Sprintf("_test-%s.example.com", suffix),
			"records": []string{},
		},
	})

	_, err := terraform.InitAndPlanE(t, opts)
	assert.Error(t, err, "plan must fail when records is empty")
}

//go:build terratest

package basic_test

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
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

// tfStateDocument runs "terraform show -json" and returns the parsed full state document.
func tfStateDocument(t *testing.T, ctx testctx.TestContext) map[string]interface{} {
	t.Helper()

	output, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "show", "-json")
	require.NoError(t, err, "terraform show -json must not fail")

	var stateDoc map[string]interface{}
	err = json.Unmarshal([]byte(output), &stateDoc)
	require.NoError(t, err, "terraform show -json output must be valid JSON")

	return stateDoc
}

// findResourcesInModule traverses a module node recursively and collects all resource entries
// whose type matches resourceType.
func findResourcesInModule(node map[string]interface{}, resourceType string) []map[string]interface{} {
	var found []map[string]interface{}
	traverseModuleNode(node, resourceType, &found)
	return found
}

func traverseModuleNode(node map[string]interface{}, resourceType string, found *[]map[string]interface{}) {
	resources, _ := node["resources"].([]interface{})
	for _, r := range resources {
		res, ok := r.(map[string]interface{})
		if !ok {
			continue
		}
		if res["type"] == resourceType {
			*found = append(*found, res)
		}
	}

	childModules, _ := node["child_modules"].([]interface{})
	for _, cm := range childModules {
		child, ok := cm.(map[string]interface{})
		if !ok {
			continue
		}
		traverseModuleNode(child, resourceType, found)
	}
}

// getRootModule extracts the root_module node from the full JSON state document.
func getRootModule(t *testing.T, stateDoc map[string]interface{}) map[string]interface{} {
	t.Helper()

	values, ok := stateDoc["values"].(map[string]interface{})
	require.True(t, ok, "terraform show -json output must have values object")

	rootModule, ok := values["root_module"].(map[string]interface{})
	require.True(t, ok, "terraform show -json output must have root_module")

	return rootModule
}

// countResourceTypeInStateList counts occurrences of a resource type in terraform state list output.
// It matches lines containing ".<resourceType>." or ending with ".<resourceType>".
func countResourceTypeInStateList(stateList string, resourceType string) int {
	count := 0
	for _, line := range strings.Split(stateList, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.Contains(trimmed, "."+resourceType+".") ||
			strings.HasSuffix(trimmed, "."+resourceType) {
			count++
		}
	}
	return count
}

// TestDnsProdZoneBasic applies the basic example once and runs all happy-path assertions
// as subtests. Using Go's t.Run subtest hierarchy ensures the framework-registered
// t.Cleanup (terraform destroy) executes after ALL subtests complete -- not after the
// first subtest returns as happens when sync.Once is used across top-level test functions.
func TestDnsProdZoneBasic(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("dns-prod-zone-basic-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	t.Run("ZoneIDOutput", func(t *testing.T) {
		zoneID := terraform.Output(t, ctx.Terraform, "zone_id")
		assert.Regexp(t, regexp.MustCompile(`^Z`), zoneID, "zone_id must match ^Z (Route 53 hosted zone ID)")
	})

	t.Run("NameServersOutput", func(t *testing.T) {
		nameServers := terraform.OutputList(t, ctx.Terraform, "name_servers")
		assert.Len(t, nameServers, 4, "name_servers must have exactly 4 entries")
		for _, ns := range nameServers {
			assert.NotEmpty(t, ns, "each name server entry must be non-empty")
		}
	})

	t.Run("ZoneARNOutput", func(t *testing.T) {
		zoneARN := terraform.Output(t, ctx.Terraform, "zone_arn")
		assert.NotEmpty(t, zoneARN, "zone_arn must not be empty")
		assert.Regexp(t, regexp.MustCompile(`^arn:aws:route53:::`), zoneARN, "zone_arn must match the Route 53 ARN shape")
	})

	t.Run("KMSKeyARNOutput", func(t *testing.T) {
		kmsKeyARN := terraform.Output(t, ctx.Terraform, "kms_key_arn")
		assert.Regexp(t, regexp.MustCompile(`^arn:aws:kms:`), kmsKeyARN, "kms_key_arn must match ^arn:aws:kms:")
	})

	t.Run("KMSKeyIDOutput", func(t *testing.T) {
		kmsKeyID := terraform.Output(t, ctx.Terraform, "kms_key_id")
		assert.NotEmpty(t, kmsKeyID, "kms_key_id must not be empty")
	})

	t.Run("Route53ZoneResourceCount", func(t *testing.T) {
		stateList, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		require.NoError(t, err, "terraform state list must not fail")

		count := countResourceTypeInStateList(stateList, "aws_route53_zone")
		assert.Equal(t, 1, count, "exactly 1 aws_route53_zone must be created")
	})

	t.Run("KMSKeyResourceCount", func(t *testing.T) {
		stateList, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		require.NoError(t, err, "terraform state list must not fail")

		count := countResourceTypeInStateList(stateList, "aws_kms_key")
		assert.Equal(t, 1, count, "exactly 1 aws_kms_key must be created (single prod DNS/cert CMK per docs/terragrunt-concepts.md)")
	})

	t.Run("KMSAliasResourceCount", func(t *testing.T) {
		stateList, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		require.NoError(t, err, "terraform state list must not fail")

		count := countResourceTypeInStateList(stateList, "aws_kms_alias")
		assert.Equal(t, 1, count, "exactly 1 aws_kms_alias must be created")
	})

	t.Run("SSMParameterForEachMapSize", func(t *testing.T) {
		stateList, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		require.NoError(t, err, "terraform state list must not fail")

		count := countResourceTypeInStateList(stateList, "aws_ssm_parameter")
		assert.Equal(t, 1, count, "exactly 1 aws_ssm_parameter must be created (one per ssm_parameters map key)")
	})

	t.Run("NoCrossAccountKmsGrantWhenDisabled", func(t *testing.T) {
		// The cross-account state-read kms:Decrypt grant is gated behind
		// tfstate_cmk_decrypt_grantee_arns; the basic example leaves it at its empty
		// default, so the module must create zero aws_kms_grant resources (and perform
		// no plan-time data.aws_kms_key read). The foundation-tier dns-prod-zone leaf
		// enables it; the standalone module never does.
		stateList, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		require.NoError(t, err, "terraform state list must not fail")

		count := countResourceTypeInStateList(stateList, "aws_kms_grant")
		assert.Equal(t, 0, count, "no aws_kms_grant must be created when tfstate_cmk_decrypt_grantee_arns is empty (default; gate off)")
	})

	t.Run("NoCrossAccountBucketPolicyWhenDisabled", func(t *testing.T) {
		// The cross-account state-read bucket policy is gated behind
		// tfstate_bucket_read_grantee_arns; the basic example leaves it at its empty
		// default, so the module must create zero aws_s3_bucket_policy resources.
		stateList, err := terraform.RunTerraformCommandE(t, ctx.Terraform, "state", "list")
		require.NoError(t, err, "terraform state list must not fail")

		count := countResourceTypeInStateList(stateList, "aws_s3_bucket_policy")
		assert.Equal(t, 0, count, "no aws_s3_bucket_policy must be created when tfstate_bucket_read_grantee_arns is empty (default; gate off)")
	})

	t.Run("KMSKeyRotationEnabled", func(t *testing.T) {
		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsKeys := findResourcesInModule(rootModule, "aws_kms_key")
		require.Len(t, kmsKeys, 1, "exactly 1 aws_kms_key must be in state")

		values, ok := kmsKeys[0]["values"].(map[string]interface{})
		require.True(t, ok, "aws_kms_key state entry must have values map")

		enableRotation, ok := values["enable_key_rotation"]
		require.True(t, ok, "aws_kms_key state values must have enable_key_rotation attribute")
		assert.Equal(t, true, enableRotation, "enable_key_rotation must be true for the prod DNS/cert CMK")
	})

	t.Run("KMSAliasTelemetryConfig", func(t *testing.T) {
		// In production the foundation-tier unit creates alias/telemetry-config
		// (docs/terragrunt-concepts.md). Because a KMS alias is unique per
		// account+region, the fixture run-scopes its alias suffix with the per-run
		// id so it never collides with the live foundation CMK; the expected name
		// is therefore alias/telemetry-config-<run-id>, derived from the same
		// TERRATEST_RUN_ID the framework injects as var.terratest_run_id.
		runID := os.Getenv("TERRATEST_RUN_ID")
		require.NotEmpty(t, runID, "TERRATEST_RUN_ID must be set to derive the expected run-scoped alias name")
		expectedAlias := fmt.Sprintf("alias/telemetry-config-%s", runID)

		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsAliases := findResourcesInModule(rootModule, "aws_kms_alias")
		require.Len(t, kmsAliases, 1, "exactly 1 aws_kms_alias must be in state")

		values, ok := kmsAliases[0]["values"].(map[string]interface{})
		require.True(t, ok, "aws_kms_alias state entry must have values map")

		aliasName, ok := values["name"]
		require.True(t, ok, "aws_kms_alias state values must have name attribute")
		assert.Equal(t, expectedAlias, aliasName, "KMS alias must be the run-scoped telemetry-config alias (alias/telemetry-config-<run-id>) per docs/terragrunt-concepts.md")
	})

	t.Run("KMSKeyPolicyViaService", func(t *testing.T) {
		// Derive the expected region from the test environment rather than asserting a literal.
		// AWS SDKs and providers honour AWS_DEFAULT_REGION first, then AWS_REGION.
		expectedRegion := os.Getenv("AWS_DEFAULT_REGION")
		if expectedRegion == "" {
			expectedRegion = os.Getenv("AWS_REGION")
		}
		require.NotEmpty(t, expectedRegion,
			"AWS_DEFAULT_REGION or AWS_REGION must be set in the test environment to derive the expected kms:ViaService value")
		expectedViaService := fmt.Sprintf("ssm.%s.amazonaws.com", expectedRegion)

		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsKeys := findResourcesInModule(rootModule, "aws_kms_key")
		require.Len(t, kmsKeys, 1, "exactly 1 aws_kms_key must be in state")

		values, ok := kmsKeys[0]["values"].(map[string]interface{})
		require.True(t, ok, "aws_kms_key state entry must have values map")

		policyRaw, ok := values["policy"]
		require.True(t, ok, "aws_kms_key state values must have policy attribute")

		policyStr, ok := policyRaw.(string)
		require.True(t, ok, "aws_kms_key policy must be a string")
		require.NotEmpty(t, policyStr, "aws_kms_key policy must not be empty")

		var policyDoc map[string]interface{}
		err := json.Unmarshal([]byte(policyStr), &policyDoc)
		require.NoError(t, err, "aws_kms_key policy must be valid JSON")

		stmts, ok := policyDoc["Statement"].([]interface{})
		require.True(t, ok, "policy must have Statement array")

		viaServiceFound := false
		for _, s := range stmts {
			stmt, ok := s.(map[string]interface{})
			if !ok {
				continue
			}
			cond, ok := stmt["Condition"].(map[string]interface{})
			if !ok {
				continue
			}
			strEquals, ok := cond["StringEquals"].(map[string]interface{})
			if !ok {
				continue
			}
			if val, ok := strEquals["kms:ViaService"]; ok {
				assert.Equal(t, expectedViaService, val,
					"kms:ViaService condition must be scoped to %s (derived from test environment region)", expectedViaService)
				viaServiceFound = true
			}
		}
		assert.True(t, viaServiceFound, "KMS key policy must contain a kms:ViaService=%s condition statement", expectedViaService)
	})

	t.Run("KMSKeyPolicyCloudWatchLogsGrant", func(t *testing.T) {
		// The telemetry-config CMK encrypts the portal WAF CloudWatch log group
		// (portal wires waf_log_kms_key_arn = this key). CloudWatch Logs validates
		// the encrypting CMK grants the logs service principal at log-group create
		// time, so the key policy MUST contain a statement granting
		// logs.<region>.amazonaws.com the CloudWatch Logs CMK actions scoped by an
		// ArnLike kms:EncryptionContext:aws:logs:arn condition (docs/terragrunt-concepts.md
		// row S4; AWS CloudWatch Logs CMK docs). region is derived from the test
		// environment, never a literal.
		expectedRegion := os.Getenv("AWS_DEFAULT_REGION")
		if expectedRegion == "" {
			expectedRegion = os.Getenv("AWS_REGION")
		}
		require.NotEmpty(t, expectedRegion,
			"AWS_DEFAULT_REGION or AWS_REGION must be set in the test environment to derive the expected logs service principal")
		expectedLogsPrincipal := fmt.Sprintf("logs.%s.amazonaws.com", expectedRegion)

		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsKeys := findResourcesInModule(rootModule, "aws_kms_key")
		require.Len(t, kmsKeys, 1, "exactly 1 aws_kms_key must be in state")

		values, ok := kmsKeys[0]["values"].(map[string]interface{})
		require.True(t, ok, "aws_kms_key state entry must have values map")

		policyStr, ok := values["policy"].(string)
		require.True(t, ok, "aws_kms_key policy must be a string")
		require.NotEmpty(t, policyStr, "aws_kms_key policy must not be empty")

		var policyDoc map[string]interface{}
		err := json.Unmarshal([]byte(policyStr), &policyDoc)
		require.NoError(t, err, "aws_kms_key policy must be valid JSON")

		stmts, ok := policyDoc["Statement"].([]interface{})
		require.True(t, ok, "policy must have Statement array")

		requiredActions := []string{
			"kms:Encrypt",
			"kms:Decrypt",
			"kms:ReEncrypt*",
			"kms:GenerateDataKey*",
			"kms:DescribeKey",
		}

		logsStatementFound := false
		for _, s := range stmts {
			stmt, ok := s.(map[string]interface{})
			if !ok {
				continue
			}
			principal, ok := stmt["Principal"].(map[string]interface{})
			if !ok {
				continue
			}
			svc, ok := principal["Service"].(string)
			if !ok || svc != expectedLogsPrincipal {
				continue
			}
			logsStatementFound = true

			// Actions must include the full CloudWatch Logs CMK action set.
			actionsRaw, ok := stmt["Action"].([]interface{})
			require.True(t, ok, "CloudWatch Logs statement must have an Action array")
			actions := make(map[string]bool, len(actionsRaw))
			for _, a := range actionsRaw {
				if as, ok := a.(string); ok {
					actions[as] = true
				}
			}
			for _, want := range requiredActions {
				assert.True(t, actions[want],
					"CloudWatch Logs statement must grant %s; got actions %v", want, actionsRaw)
			}

			// Condition must scope the grant via ArnLike on the logs encryption context.
			cond, ok := stmt["Condition"].(map[string]interface{})
			require.True(t, ok, "CloudWatch Logs statement must have a Condition")
			arnLike, ok := cond["ArnLike"].(map[string]interface{})
			require.True(t, ok, "CloudWatch Logs statement Condition must use ArnLike")
			ctxARN, ok := arnLike["kms:EncryptionContext:aws:logs:arn"].(string)
			require.True(t, ok, "ArnLike must key on kms:EncryptionContext:aws:logs:arn")
			assert.Contains(t, ctxARN, fmt.Sprintf("arn:aws:logs:%s:", expectedRegion),
				"encryption-context ARN must be scoped to the test region's log groups")
			assert.Contains(t, ctxARN, ":log-group:",
				"encryption-context ARN must scope to log groups")
		}
		assert.True(t, logsStatementFound,
			"KMS key policy must contain a statement granting %s the CloudWatch Logs CMK actions", expectedLogsPrincipal)
	})

	t.Run("KMSKeyPolicyCostAnomalyGrant", func(t *testing.T) {
		// The telemetry-config CMK (this key) also encrypts the observability alerts SNS
		// topic. AWS Cost Anomaly Detection publishes from the costalerts.amazonaws.com
		// service principal and must be granted kms:GenerateDataKey*/kms:Decrypt on the
		// encrypting CMK, or publishing to the encrypted topic fails. The grant is scoped
		// by an aws:SourceAccount StringEquals condition (AWS-recommended least privilege).
		const expectedCostAnomalyPrincipal = "costalerts.amazonaws.com"

		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsKeys := findResourcesInModule(rootModule, "aws_kms_key")
		require.Len(t, kmsKeys, 1, "exactly 1 aws_kms_key must be in state")

		values, ok := kmsKeys[0]["values"].(map[string]interface{})
		require.True(t, ok, "aws_kms_key state entry must have values map")

		policyStr, ok := values["policy"].(string)
		require.True(t, ok, "aws_kms_key policy must be a string")
		require.NotEmpty(t, policyStr, "aws_kms_key policy must not be empty")

		var policyDoc map[string]interface{}
		err := json.Unmarshal([]byte(policyStr), &policyDoc)
		require.NoError(t, err, "aws_kms_key policy must be valid JSON")

		stmts, ok := policyDoc["Statement"].([]interface{})
		require.True(t, ok, "policy must have Statement array")

		requiredActions := []string{
			"kms:GenerateDataKey*",
			"kms:Decrypt",
		}

		costAnomalyStatementFound := false
		for _, s := range stmts {
			stmt, ok := s.(map[string]interface{})
			if !ok {
				continue
			}
			principal, ok := stmt["Principal"].(map[string]interface{})
			if !ok {
				continue
			}
			svc, ok := principal["Service"].(string)
			if !ok || svc != expectedCostAnomalyPrincipal {
				continue
			}
			costAnomalyStatementFound = true

			assert.Equal(t, "Allow", stmt["Effect"], "cost-anomaly KMS statement must be Allow")

			actionsRaw, ok := stmt["Action"].([]interface{})
			require.True(t, ok, "cost-anomaly KMS statement must have an Action array")
			actions := make(map[string]bool, len(actionsRaw))
			for _, a := range actionsRaw {
				if as, ok := a.(string); ok {
					actions[as] = true
				}
			}
			for _, want := range requiredActions {
				assert.True(t, actions[want],
					"cost-anomaly KMS statement must grant %s; got actions %v", want, actionsRaw)
			}

			cond, ok := stmt["Condition"].(map[string]interface{})
			require.True(t, ok, "cost-anomaly KMS statement must have a Condition")
			strEquals, ok := cond["StringEquals"].(map[string]interface{})
			require.True(t, ok, "cost-anomaly KMS statement Condition must use StringEquals")
			srcAccount, ok := strEquals["aws:SourceAccount"].(string)
			require.True(t, ok, "cost-anomaly KMS statement must scope by aws:SourceAccount")
			assert.Regexp(t, regexp.MustCompile(`^[0-9]{12}$`), srcAccount,
				"aws:SourceAccount must be the 12-digit owner account id")
		}
		assert.True(t, costAnomalyStatementFound,
			"KMS key policy must contain a statement granting %s kms:GenerateDataKey*/kms:Decrypt so Cost Anomaly Detection can publish to the CMK-encrypted SNS topic", expectedCostAnomalyPrincipal)
	})

	t.Run("KMSKeyPolicyAdminStatementPreserved", func(t *testing.T) {
		// Adding the cost-anomaly grant must NOT remove or weaken the account-root admin
		// statement: the EnableKeyAdministration statement granting the configured
		// principals the full key-administration action set must remain intact.
		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsKeys := findResourcesInModule(rootModule, "aws_kms_key")
		require.Len(t, kmsKeys, 1, "exactly 1 aws_kms_key must be in state")

		values, ok := kmsKeys[0]["values"].(map[string]interface{})
		require.True(t, ok, "aws_kms_key state entry must have values map")

		policyStr, ok := values["policy"].(string)
		require.True(t, ok, "aws_kms_key policy must be a string")

		var policyDoc map[string]interface{}
		require.NoError(t, json.Unmarshal([]byte(policyStr), &policyDoc),
			"aws_kms_key policy must be valid JSON")

		stmts, ok := policyDoc["Statement"].([]interface{})
		require.True(t, ok, "policy must have Statement array")

		adminFound := false
		for _, s := range stmts {
			stmt, ok := s.(map[string]interface{})
			if !ok {
				continue
			}
			if sid, _ := stmt["Sid"].(string); sid == "EnableKeyAdministration" {
				adminFound = true
				assert.Equal(t, "Allow", stmt["Effect"], "admin statement must remain Allow")
			}
		}
		assert.True(t, adminFound,
			"EnableKeyAdministration statement must remain in the key policy after adding the cost-anomaly grant")
	})

	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	t.Run("ValidateAndFmt", func(t *testing.T) {
		zoneID := terraform.Output(t, ctx.Terraform, "zone_id")
		assert.NotEmpty(t, zoneID, "zone_id must be non-empty, confirming validate + apply passed")
	})

	t.Run("RequiredOutputsNotEmpty", func(t *testing.T) {
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "zone_id"), "zone_id must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "zone_arn"), "zone_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "kms_key_arn"), "kms_key_arn must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "kms_key_id"), "kms_key_id must not be empty")
		assert.NotEmpty(t, terraform.Output(t, ctx.Terraform, "name_servers"), "name_servers must not be empty")
	})

	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode, "second plan must show zero resource changes (exit code 0 means no changes)")
	})

	t.Run("NoExtraKMSKeys", func(t *testing.T) {
		stateDoc := tfStateDocument(t, ctx)
		rootModule := getRootModule(t, stateDoc)

		kmsAliases := findResourcesInModule(rootModule, "aws_kms_alias")
		for _, alias := range kmsAliases {
			values, ok := alias["values"].(map[string]interface{})
			if !ok {
				continue
			}
			name, _ := values["name"].(string)
			assert.NotEqual(t, "alias/telemetry-data", name,
				"dns-prod-zone must NOT create alias/telemetry-data (owned by the data-lake reference per docs/terragrunt-concepts.md)")
			assert.NotEqual(t, "alias/telemetry-spice", name,
				"dns-prod-zone must NOT create alias/telemetry-spice (owned by its deploy-layer owner per docs/terragrunt-concepts.md)")
		}
	})
}

// TestDnsProdZoneVariableValidationZoneName asserts that zone_name validation is wired in the
// reference module by confirming the plan fails for an invalid zone_name value. The reference
// module root is targeted directly (not via the example fixture, which derives zone_name from
// a data source) so the validation block in variables.tf is exercised.
func TestDnsProdZoneVariableValidationZoneName(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	validationRegion := os.Getenv("AWS_DEFAULT_REGION")
	if validationRegion == "" {
		validationRegion = os.Getenv("AWS_REGION")
	}
	require.NotEmpty(t, validationRegion, "AWS_DEFAULT_REGION or AWS_REGION must be set in the test environment")

	directOpts := testctx.InitTerraform("../../", testctx.TestConfig{
		Name: fmt.Sprintf("dns-prod-zone-invalid-zone-%s", suffix),
		ExtraVars: map[string]interface{}{
			"zone_name": "INVALID_UPPERCASE_ZONE",
			"kms_alias": "telemetry-config",
			"region":    validationRegion,
			"ssm_parameters": map[string]interface{}{
				"/telemetry/dns/zone-id": map[string]interface{}{
					"type":  "String",
					"value": "placeholder",
				},
			},
			"kms_key_principals": []interface{}{
				"arn:aws:iam::123456789012:root",
			},
		},
	})

	_, err := terraform.InitAndPlanE(t, directOpts)
	assert.Error(t, err, "plan must fail when zone_name contains uppercase characters (validation block in variables.tf)")
}

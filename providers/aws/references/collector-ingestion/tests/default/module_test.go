//go:build terratest

package default_test

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/applicationautoscaling"
	"github.com/aws/aws-sdk-go-v2/service/applicationautoscaling/types"
	"github.com/aws/aws-sdk-go-v2/service/ecs"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/assertions"
	"github.com/caylent-solutions/terraform-terratest-framework/pkg/testctx"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// mustTaggingVars reads PROJECT_TAG, TERRATEST_RUN_ID, and AWS_ACCOUNT_ID from the
// environment. It calls t.Fatal with an actionable error message when any of them is
// unset. ExtraVars override committed tfvars values (spec section 4.3, D-12).
//
// AWS_ACCOUNT_ID is exported by the make tf-test harness (scripts/run_terratest.py)
// from the live STS caller identity and is injected here as var.account_id, which the
// fixture uses as the GLOBAL-uniqueness suffix for the firehose-destination and
// access-log DESTINATION bucket names. The Terraform default ("000000000000") is a
// statically-resolvable placeholder that lets the offline trivy scan prove access
// logging is enabled; the real applied run always overrides it with the caller account
// id so concurrent runs in different accounts never collide on a global S3 bucket name.
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
	accountID := os.Getenv("AWS_ACCOUNT_ID")
	if accountID == "" {
		t.Fatal("ERROR: AWS_ACCOUNT_ID is not set; run via make tf-test")
	}
	return map[string]interface{}{
		"project_tag":      projectTag,
		"terratest_run_id": runID,
		"account_id":       accountID,
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

// TestCollectorIngestionDefault applies the default example ONCE and runs all
// happy-path assertions as sub-tests sharing the same applied Terraform state.
// The parent test's t.Cleanup (registered by testctx.RunSingleExample on the
// parent t) destroys the state only AFTER all sub-tests complete. This avoids
// the sync.Once + per-test-cleanup race where the first individual test's cleanup
// fired terraform destroy before the remaining tests read the outputs/state
// (matching the established parent-test pattern in vpc-network/data-lake/observability).
func TestCollectorIngestionDefault(t *testing.T) {
	require.NotEmpty(t, os.Getenv("AWS_DEFAULT_REGION"), "AWS_DEFAULT_REGION must be set")
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "default", testctx.TestConfig{
		Name: fmt.Sprintf("collector-ingestion-default-%s", suffix),
		// structured_otlp_service_names exercises the dual-logs-pipeline (logs/structured) in
		// the SAME apply as every other assertion in this parent test (single-apply idiom -- no
		// second RunSingleExample). The empty-list inert path is NOT re-verified by a second
		// apply here; it is covered by the module's fmt/validate and the data-lake side.
		ExtraVars: mergeExtraVars(t, map[string]interface{}{
			"structured_otlp_service_names": []string{"claude-code", "claude-cowork"},
		}, mustTaggingVars(t)),
	})

	// CloudfrontDomainNameOutput asserts cloudfront_domain_name output is non-empty (AC-1).
	t.Run("CloudfrontDomainNameOutput", func(t *testing.T) {
		domainName := terraform.Output(t, ctx.Terraform, "cloudfront_domain_name")
		assert.NotEmpty(t, domainName,
			"cloudfront_domain_name must not be empty (AC-1)")
		assert.Contains(t, domainName, "cloudfront.net",
			"cloudfront_domain_name must be a CloudFront distribution domain (AC-1)")
	})

	// CloudfrontHostedZoneIdOutput asserts cloudfront_hosted_zone_id output is non-empty (AC-1).
	t.Run("CloudfrontHostedZoneIdOutput", func(t *testing.T) {
		hostedZoneId := terraform.Output(t, ctx.Terraform, "cloudfront_hosted_zone_id")
		assert.NotEmpty(t, hostedZoneId,
			"cloudfront_hosted_zone_id must not be empty (AC-1)")
	})

	// AlbArnOutput asserts alb_arn output is non-empty and matches the ALB ARN pattern (AC-1).
	t.Run("AlbArnOutput", func(t *testing.T) {
		albArn := terraform.Output(t, ctx.Terraform, "alb_arn")
		assert.NotEmpty(t, albArn,
			"alb_arn must not be empty")
		assert.True(t,
			strings.HasPrefix(albArn, "arn:aws:elasticloadbalancing:"),
			"alb_arn must match ALB ARN pattern, got: %s", albArn)
	})

	// EcsServiceNameOutput asserts ecs_service_name output is non-empty (AC-1).
	t.Run("EcsServiceNameOutput", func(t *testing.T) {
		serviceName := terraform.Output(t, ctx.Terraform, "ecs_service_name")
		assert.NotEmpty(t, serviceName,
			"ecs_service_name must not be empty")
	})

	// FirehoseArnIsInput asserts firehose_delivery_stream_arn is declared as a variable
	// (input) and NOT as a computed output (D37, AC-3).
	t.Run("FirehoseArnIsInput", func(t *testing.T) {
		// The reference must expose firehose_delivery_stream_arn as an input echo
		// to allow the test to confirm it passes through without recomputing.
		firehoseArnEcho := terraform.Output(t, ctx.Terraform, "firehose_delivery_stream_arn_echo")
		assert.NotEmpty(t, firehoseArnEcho,
			"firehose_delivery_stream_arn_echo must not be empty (D37 input contract)")

		// Verify the variable file on disk declares it as a variable, not a resource output.
		variablesContent, err := os.ReadFile("../../variables.tf")
		require.NoError(t, err, "variables.tf must exist and be readable")
		assert.Contains(t, string(variablesContent), `variable "firehose_delivery_stream_arn"`,
			"firehose_delivery_stream_arn must be declared as a variable (D37 -- input, not output)")
	})

	// NoAcmCertificateResource asserts the reference creates aws_acm_certificate_validation
	// but does NOT create aws_acm_certificate (AC-9). Certs live in acm-collector only.
	t.Run("NoAcmCertificateResource", func(t *testing.T) {
		// aws_acm_certificate_validation must exist (the reference performs validation).
		assertions.AssertResourceCount(t, ctx, "aws_acm_certificate_validation", 1)

		// aws_acm_certificate must NOT exist (certs live in acm-collector only).
		assertions.AssertResourceCount(t, ctx, "aws_acm_certificate", 0)
	})

	// CloudfrontDistributionExists asserts the CloudFront distribution resource is created (AC-9).
	t.Run("CloudfrontDistributionExists", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_cloudfront_distribution", 1)
	})

	// WafWebAclExists asserts the WAF WebACL resource is created (AC-9).
	t.Run("WafWebAclExists", func(t *testing.T) {
		assertions.AssertResourceCount(t, ctx, "aws_wafv2_web_acl", 1)
	})

	// WafManagedRuleGroups asserts the WAF WebACL is configured with the three payload/
	// known-malicious managed rule groups per docs/terragrunt-concepts.md (AC-9), and
	// that AWSManagedRulesAnonymousIpList is DELIBERATELY ABSENT so the public collector
	// can ingest usage telemetry from any tool on any (cloud/CI) network -- only
	// known-malicious IPs are blocked (AmazonIpReputationList). See locals.tf
	// "OPEN-INGESTION POSTURE".
	t.Run("WafManagedRuleGroups", func(t *testing.T) {
		// The reference must expose the WAF managed rule group names for assertion.
		wafRulesOutput := terraform.Output(t, ctx.Terraform, "waf_managed_rule_names")
		require.NotEmpty(t, wafRulesOutput,
			"waf_managed_rule_names must be exposed for rule group assertions (AC-9)")

		assert.Contains(t, wafRulesOutput, "AWSManagedRulesCommonRuleSet",
			"WAF must include AWSManagedRulesCommonRuleSet at priority 10 per docs/terragrunt-concepts.md")
		assert.Contains(t, wafRulesOutput, "AWSManagedRulesKnownBadInputsRuleSet",
			"WAF must include AWSManagedRulesKnownBadInputsRuleSet at priority 20 per docs/terragrunt-concepts.md")
		assert.Contains(t, wafRulesOutput, "AWSManagedRulesAmazonIpReputationList",
			"WAF must include AWSManagedRulesAmazonIpReputationList at priority 30 (blocks known-malicious IPs)")
		assert.NotContains(t, wafRulesOutput, "AWSManagedRulesAnonymousIpList",
			"WAF must NOT include AWSManagedRulesAnonymousIpList: it blocks hosting/cloud/VPN source IPs (e.g. CI runners), which are legitimate telemetry emitters for this public collector (open-ingestion posture)")
	})

	// WafRateLimitPerIpSet asserts rate_limit_per_ip is set to its concrete value
	// (default 2000 per D44) in the WAF configuration (AC-9).
	t.Run("WafRateLimitPerIpSet", func(t *testing.T) {
		rateLimitOutput := terraform.Output(t, ctx.Terraform, "waf_rate_limit_per_ip_echo")
		assert.NotEmpty(t, rateLimitOutput,
			"waf_rate_limit_per_ip_echo must not be empty (D44 concrete value required)")
		assert.NotEqual(t, "0", rateLimitOutput,
			"rate_limit_per_ip must not be zero -- D44 requires a concrete non-zero default")
	})

	// WafLoggingEnabled asserts the WAF WebACL has logging enabled and the log_kms_key_arn
	// is the passed-in telemetry-data CMK (AC-9, docs/terragrunt-concepts.md).
	t.Run("WafLoggingEnabled", func(t *testing.T) {
		loggingEnabledOutput := terraform.Output(t, ctx.Terraform, "waf_logging_enabled_echo")
		assert.Equal(t, "true", loggingEnabledOutput,
			"WAF logging_enabled must be true per docs/terragrunt-concepts.md (AC-9)")

		wafLogKmsKeyArn := terraform.Output(t, ctx.Terraform, "waf_log_kms_key_arn_echo")
		assert.NotEmpty(t, wafLogKmsKeyArn,
			"waf_log_kms_key_arn must be set to the telemetry-data CMK per docs/terragrunt-concepts.md (AC-9)")
		assert.True(t,
			strings.HasPrefix(wafLogKmsKeyArn, "arn:aws:kms:"),
			"waf_log_kms_key_arn must be a valid KMS ARN, got: %s", wafLogKmsKeyArn)
	})

	// WafCommonRuleSetBodySizeOverride asserts BUG-1 is fixed against the APPLIED WAF web ACL:
	// the AWSManagedRulesCommonRuleSet statement overrides ONLY the SizeRestrictions_BODY sub-rule
	// to "count" (so legitimate large OTLP request bodies up to the 4 MiB ADOT receiver cap are
	// counted, not 403'd), while EVERY managed rule group's group-level override_action stays
	// "none" (every other rule -- SQLi, XSS, LFI, the other three groups -- stays fully enforced).
	// The apply itself is a real check: AWS WAFv2 rejects a rule_action_override whose name is not
	// a real sub-rule of the managed group, so a green apply proves SizeRestrictions_BODY is valid.
	t.Run("WafCommonRuleSetBodySizeOverride", func(t *testing.T) {
		raw := terraform.Output(t, ctx.Terraform, "waf_managed_rule_groups_echo")
		require.NotEmpty(t, raw,
			"waf_managed_rule_groups_echo must be non-empty (BUG-1 SizeRestrictions_BODY override assertion)")

		var groups []struct {
			Name                string `json:"name"`
			VendorName          string `json:"vendor_name"`
			Priority            int    `json:"priority"`
			OverrideAction      string `json:"override_action"`
			RuleActionOverrides []struct {
				Name        string `json:"name"`
				ActionToUse string `json:"action_to_use"`
			} `json:"rule_action_overrides"`
		}
		require.NoError(t, json.Unmarshal([]byte(raw), &groups),
			"waf_managed_rule_groups_echo must be valid JSON, got: %s", raw)
		require.Len(t, groups, 3,
			"WAF must declare the three managed rule groups per docs/terragrunt-concepts.md (AnonymousIpList omitted -- open-ingestion posture)")

		var commonFound, bodySizeCountFound bool
		for _, g := range groups {
			// Group-level enforcement is preserved everywhere: BUG-1 neutralizes exactly ONE sub-rule.
			assert.Equal(t, "none", g.OverrideAction,
				"managed rule group %s group-level override_action must stay \"none\" (enforced); BUG-1 neutralizes only the SizeRestrictions_BODY sub-rule", g.Name)

			if g.Name == "AWSManagedRulesCommonRuleSet" {
				commonFound = true
				for _, o := range g.RuleActionOverrides {
					if o.Name == "SizeRestrictions_BODY" {
						bodySizeCountFound = true
						assert.Equal(t, "count", o.ActionToUse,
							"CommonRuleSet SizeRestrictions_BODY must be overridden to \"count\" so large OTLP bodies are not 403'd (BUG-1)")
					}
				}
			} else {
				// Only CommonRuleSet neutralizes a sub-rule; no other group overrides anything.
				assert.Empty(t, g.RuleActionOverrides,
					"managed rule group %s must declare no rule_action_overrides -- only CommonRuleSet's SizeRestrictions_BODY is neutralized (BUG-1)", g.Name)
			}
		}
		assert.True(t, commonFound,
			"AWSManagedRulesCommonRuleSet must be present (priority 10, docs/terragrunt-concepts.md)")
		assert.True(t, bodySizeCountFound,
			"AWSManagedRulesCommonRuleSet must override SizeRestrictions_BODY to count (BUG-1: WAF must not 403 large OTLP bodies up to the 4 MiB receiver cap)")
	})

	// CwlToFirehoseTrustSourceArnsAllowBothForms asserts BUG-2 is fixed: the CWL->Firehose role
	// trust-policy ArnLike aws:SourceArn allows BOTH forms CloudWatch Logs presents for this role --
	// the BARE telemetry ingest log-group ARN (used during PutSubscriptionFilter's creation-time
	// test-message delivery) AND that ARN WITH the trailing ':*' segment (used during runtime
	// delivery). A ':*'-only pattern fails filter creation ("Could not deliver test message"); a
	// bare-only pattern silently delivers zero records at runtime (data lake dark). Both forms,
	// scoped to exactly this log group, are required. That this terratest applies the filter
	// successfully (TelemetryLogHopResources) is itself proof the creation-time test message was
	// delivered, i.e. the trust now allows the bare form.
	t.Run("CwlToFirehoseTrustSourceArnsAllowBothForms", func(t *testing.T) {
		raw := terraform.Output(t, ctx.Terraform, "cwl_to_firehose_trust_source_arns_echo")
		require.NotEmpty(t, raw,
			"cwl_to_firehose_trust_source_arns_echo must be non-empty (BUG-2 trust SourceArn assertion)")

		var sourceArns []string
		require.NoError(t, json.Unmarshal([]byte(raw), &sourceArns),
			"cwl_to_firehose_trust_source_arns_echo must be a valid JSON string list, got: %s", raw)

		logGroupArn := terraform.Output(t, ctx.Terraform, "telemetry_log_group_arn")
		require.NotEmpty(t, logGroupArn,
			"telemetry_log_group_arn must be non-empty to compare against the trust SourceArns")
		require.True(t, strings.HasPrefix(logGroupArn, "arn:aws:logs:"),
			"telemetry_log_group_arn must be a CloudWatch Logs ARN, got: %s", logGroupArn)

		assert.Contains(t, sourceArns, logGroupArn,
			"trust SourceArns must include the BARE log-group ARN so PutSubscriptionFilter's creation-time test message can be delivered (BUG-2)")
		assert.Contains(t, sourceArns, logGroupArn+":*",
			"trust SourceArns must include the log-group ARN with the trailing ':*' so runtime delivery is authorized (BUG-2)")
	})

	// AdotConfigMemoryLimiter asserts the rendered ADOT AOT_CONFIG_CONTENT includes a
	// memory_limiter processor (D5, AC-9).
	t.Run("AdotConfigMemoryLimiter", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert ADOT config requirements (AC-9)")

		assert.Contains(t, adotConfig, "memory_limiter",
			"ADOT config must include memory_limiter processor per D5 (AC-9)")
	})

	// AdotConfigHealthCheckEndpoint asserts the rendered ADOT AOT_CONFIG_CONTENT exposes the
	// health_check extension on a reachable 0.0.0.0:13133 endpoint (not localhost:13133, which
	// the internal ALB cannot reach) and keeps health_check in service.extensions.
	t.Run("AdotConfigHealthCheckEndpoint", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert the health_check endpoint")

		// The health_check extension must bind 0.0.0.0:13133 so the internal ALB can reach it.
		assert.Contains(t, adotConfig, "health_check",
			"ADOT config must declare the health_check extension")
		assert.Contains(t, adotConfig, "0.0.0.0:13133",
			"ADOT config health_check endpoint must bind 0.0.0.0:13133 so the internal ALB target-group "+
				"health check can reach it (localhost:13133 is loopback-only and unreachable)")
		// The default localhost binding must NOT be used (it would make the ALB health check fail).
		assert.NotContains(t, adotConfig, "localhost:13133",
			"ADOT config health_check endpoint must not be the default localhost:13133 (unreachable by the ALB)")

		// health_check must remain wired into service.extensions so the extension actually starts.
		endpointIdx := strings.Index(adotConfig, "0.0.0.0:13133")
		require.GreaterOrEqual(t, endpointIdx, 0, "health_check endpoint must be present")
		serviceIdx := strings.Index(adotConfig, "\"extensions\":\n  - \"health_check\"")
		assert.GreaterOrEqual(t, serviceIdx, 0,
			"service.extensions must list health_check so the extension starts (rendered YAML)")
	})

	// AlbHealthCheckProbesAdotHealthPort asserts the ALB target-group health check probes the
	// ADOT health_check extension (port 13133, path "/", matcher 200) and NOT the OTLP/HTTP
	// receiver path on the 4318 traffic-port.
	t.Run("AlbHealthCheckProbesAdotHealthPort", func(t *testing.T) {
		port := terraform.Output(t, ctx.Terraform, "adot_target_group_health_check_port_echo")
		assert.Equal(t, "13133", port,
			"ALB target-group health check port must be 13133 (the ADOT health_check extension), not traffic-port (4318 OTLP)")

		path := terraform.Output(t, ctx.Terraform, "adot_target_group_health_check_path_echo")
		assert.Equal(t, "/", path,
			"ALB target-group health check path must be \"/\" (the basic health_check extension serves 200 on \"/\"), not /health/status")

		matcher := terraform.Output(t, ctx.Terraform, "adot_target_group_health_check_matcher_echo")
		assert.Equal(t, "200", matcher,
			"ALB target-group health check matcher must be \"200\"")

		healthPort := terraform.Output(t, ctx.Terraform, "adot_health_check_port_echo")
		assert.Equal(t, "13133", healthPort,
			"adot_health_check_port must default to 13133")
	})

	// AdotSecurityGroupAllowsHealthPort asserts the in-module ADOT task security group ingress
	// allows BOTH the OTLP/HTTP receiver port (4318) and the health-check extension port (13133)
	// from the VPC CIDR.
	t.Run("AdotSecurityGroupAllowsHealthPort", func(t *testing.T) {
		ingressPorts := terraform.Output(t, ctx.Terraform, "adot_security_group_ingress_ports_echo")
		require.NotEmpty(t, ingressPorts,
			"adot_security_group_ingress_ports_echo must be non-empty to assert the SG ingress ports")

		ports := strings.Split(ingressPorts, ",")
		assert.Contains(t, ports, "4318",
			"ADOT task SG must allow ingress on 4318 (OTLP/HTTP receiver) from the VPC CIDR")
		assert.Contains(t, ports, "13133",
			"ADOT task SG must allow ingress on 13133 (health_check extension) from the VPC CIDR so the internal ALB health check reaches it")
	})

	// AdotConfigContentTypeRestriction asserts the OTLP/HTTP receiver content-type
	// restriction is in place (D5, AC-9). The OTLP/HTTP receiver in ADOT v0.43.1 has NO
	// allowed_content_types config key: it natively accepts only OTLP payloads
	// (application/x-protobuf, application/json) and rejects any other Content-Type, and
	// declaring an explicit allowlist key makes the collector fail config validation and
	// exit (docker-validated, see locals.tf). The allowlist is therefore intrinsic, so the
	// correct assertion is that the receiver is the OTLP/HTTP receiver bound on the OTLP
	// port with a bounded request body, and that the config does NOT carry an unsupported
	// allowed_content_types / content_types key that would crash the collector.
	t.Run("AdotConfigContentTypeRestriction", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert the OTLP receiver content-type restriction (AC-9)")

		// The OTLP/HTTP receiver (whose content-type allowlist is intrinsic) must be present.
		assert.Contains(t, adotConfig, "otlp",
			"ADOT config must declare the OTLP receiver whose intrinsic content-type allowlist "+
				"restricts payloads to application/x-protobuf and application/json (D5, AC-9)")
		assert.Contains(t, adotConfig, "0.0.0.0:4318",
			"ADOT config OTLP/HTTP receiver must bind 0.0.0.0:4318 (D5, AC-9)")
		// A bounded request body is the explicit ingress restriction the receiver does carry.
		assert.Contains(t, adotConfig, "max_request_body_size",
			"ADOT config OTLP/HTTP receiver must set max_request_body_size (D5, D44, AC-9)")
		// The unsupported allowlist keys must NOT be present: setting them crashes ADOT v0.43.1.
		assert.NotContains(t, adotConfig, "allowed_content_types",
			"ADOT config must NOT declare an allowed_content_types key -- it is not a valid OTLP/HTTP "+
				"receiver key in ADOT v0.43.1 and makes the collector fail config validation (docker-validated)")
		assert.NotContains(t, adotConfig, "content_types",
			"ADOT config must NOT declare a content_types key on the OTLP receiver (unsupported, crashes the collector)")
	})

	// AdotConfigMaxRequestBodySize asserts the rendered ADOT config sets max_request_body_size
	// to the concrete input value (D5, D44, AC-9).
	t.Run("AdotConfigMaxRequestBodySize", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert max_request_body_size (AC-9)")

		// Default is 4194304 bytes per D44; the fixture uses the default.
		assert.Contains(t, adotConfig, "4194304",
			"ADOT config must include max_request_body_size value per D44 (AC-9)")
	})

	// AdotConfigMemoryLimiterRightSizedForHighConcurrency asserts the rendered ADOT config's
	// memory_limiter (and the isolated memory_limiter/metrics instance) use the right-sized
	// high-concurrency-ingestion defaults (limit_mib 1800 / spike_limit_mib 400, raised
	// proportionally from the prior 900/200 D44 defaults alongside the adot_task_memory
	// 1024 -> 2048 bump), and that limit_mib stays below adot_task_memory (fixture also uses
	// the right-sized 2048 default -- see examples/default/variables.tf).
	t.Run("AdotConfigMemoryLimiterRightSizedForHighConcurrency", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert the right-sized memory_limiter values")

		assert.Contains(t, adotConfig, `"limit_mib": 1800`,
			"ADOT config memory_limiter.limit_mib must be right-sized to 1800 (proportional to the 2048 MiB task default)")
		assert.Contains(t, adotConfig, `"spike_limit_mib": 400`,
			"ADOT config memory_limiter.spike_limit_mib must be right-sized to 400 (proportional to the 2048 MiB task default)")
	})

	// AdotConfigExporterThroughputSizedForHighConcurrency asserts the rendered ADOT config's
	// awscloudwatchlogs / awscloudwatchlogs/structured exporter sending_queue and the shared
	// batch processor carry the new input-driven high-concurrency-ingestion sizing (previously
	// unsized: sending_queue = { enabled = true }, batch = {}).
	t.Run("AdotConfigExporterThroughputSizedForHighConcurrency", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert exporter throughput sizing")

		// sending_queue sizing must appear on BOTH awscloudwatchlogs exporters (the raw path
		// and the structured path) -- the rendered YAML declares this pair twice.
		assert.Equal(t, 2, strings.Count(adotConfig, `"queue_size": 10000`),
			"both the awscloudwatchlogs and awscloudwatchlogs/structured exporters must set sending_queue.queue_size to the input-driven default (10000)")
		assert.Equal(t, 2, strings.Count(adotConfig, `"num_consumers": 8`),
			"both the awscloudwatchlogs and awscloudwatchlogs/structured exporters must set sending_queue.num_consumers to the input-driven default (8)")

		// The shared batch processor must carry the input-driven send_batch_size/timeout.
		assert.Contains(t, adotConfig, `"send_batch_size": 8192`,
			"the shared batch processor must set send_batch_size to the input-driven default (8192)")
		assert.Contains(t, adotConfig, `"timeout": "5s"`,
			"the shared batch processor must set timeout to the input-driven default (rendered \"5s\")")
	})

	// AdotConfigLogsOnlyNoMetrics asserts the rendered ADOT config exports OTLP log records
	// only with no metrics pipeline and no AMP (D27.3, AC-9).
	t.Run("AdotConfigLogsOnlyNoMetrics", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert D27.3 logs-only scope (AC-9)")

		// Logs pipeline must export via the awscloudwatchlogs exporter. The prior
		// otlp/logs exporter pointed at firehose.<region>.amazonaws.com (Firehose does
		// not speak OTLP/HTTP, so it never delivered) and has been replaced.
		assert.Contains(t, adotConfig, "awscloudwatchlogs",
			"ADOT config must declare the awscloudwatchlogs logs exporter (D27.3, AC-9)")
		assert.NotContains(t, adotConfig, "otlp/logs",
			"ADOT config must not retain the broken otlp/logs Firehose exporter (replaced by awscloudwatchlogs)")

		// raw_log = true is mandatory so log.Body().AsString() is written as the event
		// message, keeping the SDK event JSON (incl. top-level .tool) intact for the
		// downstream Firehose dynamic-partitioning and Glue column mapping.
		assert.Contains(t, adotConfig, "raw_log",
			"awscloudwatchlogs exporter must set raw_log (true) so the event body is written raw")

		// No metrics pipeline or AMP endpoint is allowed.
		assert.NotContains(t, adotConfig, "prometheusremotewrite",
			"ADOT config must not contain AMP/prometheusremotewrite exporter (D27.3, AC-9)")
		assert.NotContains(t, adotConfig, "aps-workspaces.amazonaws.com",
			"ADOT config must not reference AMP workspace endpoint (D27.3, AC-9)")
		// A metrics pipeline would contain a metrics: section in exporters.
		assert.NotContains(t, adotConfig, "otlp/metrics",
			"ADOT config must not declare an OTLP metrics pipeline (D27.3, AC-9)")
	})

	// AdotConfigCloudWatchLogsExporter asserts the rendered ADOT config targets the dedicated
	// telemetry CloudWatch Logs ingest group via the awscloudwatchlogs exporter.
	t.Run("AdotConfigCloudWatchLogsExporter", func(t *testing.T) {
		adotConfig := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, adotConfig,
			"adot_config_content_echo must be non-empty to assert the awscloudwatchlogs exporter target")

		// The exporter must target the dedicated telemetry ingest group /telemetry/<env>/ingest/otlp-logs
		// and declare a log_stream_name so it does not rely on auto-create.
		assert.Contains(t, adotConfig, "/ingest/otlp-logs",
			"awscloudwatchlogs exporter log_group_name must be the dedicated telemetry ingest group")
		assert.Contains(t, adotConfig, "log_stream_name",
			"awscloudwatchlogs exporter must declare a log_stream_name")
		assert.Contains(t, adotConfig, "log_group_name",
			"awscloudwatchlogs exporter must declare a log_group_name")
	})

	// AdotConfigStructuredLogsPipeline asserts the input-driven dual-logs-pipeline: with
	// structured_otlp_service_names non-empty (this parent test supplies claude-code and
	// claude-cowork via ExtraVars), the rendered ADOT config gets a second logs/structured
	// pipeline with filter/keep_structured, exported raw_log=false via
	// awscloudwatchlogs/structured so log-record attributes survive for the data-lake
	// cwl_split reshape, while the EXISTING logs pipeline gains filter/drop_structured and
	// keeps its raw_log=true awscloudwatchlogs exporter byte-unchanged for every other
	// (non-structured) service. The empty-list inert path is covered by the module's
	// fmt/validate and the data-lake side, not by a second apply here (single-apply idiom).
	t.Run("AdotConfigStructuredLogsPipeline", func(t *testing.T) {
		cfg := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, cfg,
			"adot_config_content_echo must be non-empty to assert the structured logs pipeline")

		// The second pipeline and its filter processors must be present.
		assert.Contains(t, cfg, "logs/structured",
			"ADOT config must declare the logs/structured pipeline when structured_otlp_service_names is non-empty")
		assert.Contains(t, cfg, "filter/keep_structured",
			"ADOT config must declare the filter/keep_structured processor on the logs/structured pipeline")
		assert.Contains(t, cfg, "filter/drop_structured",
			"ADOT config must declare the filter/drop_structured processor on the existing logs pipeline")

		// The structured exporter (raw_log=false) must carry the structured-OTLP records.
		// yamlencode renders both the quoted map key and the unquoted boolean value, i.e.
		// `"raw_log": false` (verified against this module's actual yamlencode() rendering).
		assert.Contains(t, cfg, "awscloudwatchlogs/structured",
			"ADOT config must declare the awscloudwatchlogs/structured exporter for structured-OTLP records")
		assert.Contains(t, cfg, `"raw_log": false`,
			`awscloudwatchlogs/structured exporter must set "raw_log": false so log-record attributes survive for the data-lake cwl_split reshape`)

		// The filter conditions must reference both configured structured service names.
		assert.Contains(t, cfg, "claude-code",
			"filter conditions must reference the claude-code service name")
		assert.Contains(t, cfg, "claude-cowork",
			"filter conditions must reference the claude-cowork service name")

		// NON-DISRUPTION: the original raw_log=true path and its exporter/pipeline must
		// remain byte-present for every non-structured record.
		assert.Contains(t, cfg, `"raw_log": true`,
			`the existing awscloudwatchlogs exporter must keep "raw_log": true (non-structured path byte-unchanged)`)
		assert.Contains(t, cfg, "awscloudwatchlogs",
			"the existing awscloudwatchlogs exporter must remain declared")
		assert.Contains(t, cfg, `"logs":`,
			"the existing logs pipeline (map key \"logs\") must remain declared")
	})

	// AdotConfigStructuredMetricsPipeline asserts the input-driven structured-OTLP METRICS
	// pipeline that mirrors the logs/structured routing above: with
	// structured_otlp_service_names non-empty (this parent test supplies claude-code and
	// claude-cowork via ExtraVars), the rendered ADOT config gets a metrics/structured pipeline
	// gated by filter/keep_structured_metrics (reusing the SAME keep condition as
	// filter/keep_structured), isolated memory_limiter/metrics + batch/metrics processors
	// (never the shared logs memory_limiter, so a metrics burst cannot starve example-cli/structured
	// LOG delivery), and an awsemf exporter that writes CloudWatch EMF log events to a THIRD
	// stream (adot-collector-metrics-emf) in the SAME telemetry ingest log group the logs
	// exporters target. NON-DISRUPTION: the two pre-existing LOGS pipelines (logs,
	// logs/structured) must remain present and unchanged by this addition.
	t.Run("AdotConfigStructuredMetricsPipeline", func(t *testing.T) {
		cfg := terraform.Output(t, ctx.Terraform, "adot_config_content_echo")
		require.NotEmpty(t, cfg,
			"adot_config_content_echo must be non-empty to assert the structured metrics pipeline")

		// The metrics/structured pipeline and its dedicated processors/exporter must be present.
		assert.Contains(t, cfg, `"metrics/structured":`,
			"ADOT config must declare the metrics/structured pipeline when structured_otlp_service_names is non-empty")
		assert.Contains(t, cfg, "filter/keep_structured_metrics",
			"ADOT config must declare the filter/keep_structured_metrics processor on the metrics/structured pipeline")
		assert.Contains(t, cfg, `"memory_limiter/metrics":`,
			"ADOT config must declare an ISOLATED memory_limiter/metrics processor (never the shared logs memory_limiter)")
		assert.Contains(t, cfg, `"batch/metrics":`,
			"ADOT config must declare an ISOLATED batch/metrics processor")
		assert.Contains(t, cfg, "awsemf",
			"ADOT config must declare the awsemf exporter for the metrics/structured pipeline")

		// The awsemf exporter block must carry the docker-validated keys with the correct
		// values: resource_to_telemetry_conversion enabled (else no service.name field
		// survives, and the row cannot be classified downstream), and
		// dimension_rollup_option NoDimensionRollup (the minimal-cost EMF shape).
		assert.Contains(t, cfg, `"enabled": true`,
			`awsemf exporter must set resource_to_telemetry_conversion.enabled=true so service.name survives for the data-lake reshape`)
		assert.Contains(t, cfg, "NoDimensionRollup",
			`awsemf exporter must set dimension_rollup_option: "NoDimensionRollup"`)

		// The metrics stream must be a THIRD stream, distinct from the raw awscloudwatchlogs
		// stream (adot-collector) and the structured logs stream (adot-collector-structured).
		assert.Contains(t, cfg, "adot-collector-",
			"awsemf exporter log_stream_name must be derived from the telemetry_log_stream_name base")
		assert.Contains(t, cfg, "metrics-emf",
			"awsemf exporter log_stream_name must be the dedicated metrics-emf stream")

		// The awsemf exporter must target the SAME log group as the awscloudwatchlogs
		// exporters (no new log group for metrics).
		assert.Contains(t, cfg, "/ingest/otlp-logs",
			"awsemf exporter log_group_name must be the SAME dedicated telemetry ingest group the logs exporters target")

		// NON-DISRUPTION: the two pre-existing LOGS pipelines must remain present and
		// unchanged by the addition of metrics/structured.
		assert.Contains(t, cfg, `"logs":`,
			"the existing logs pipeline (map key \"logs\") must remain declared alongside metrics/structured")
		assert.Contains(t, cfg, "logs/structured",
			"the existing logs/structured pipeline must remain declared alongside metrics/structured")
		assert.Contains(t, cfg, "filter/drop_structured",
			"the existing logs pipeline's filter/drop_structured processor must remain declared")
		assert.Contains(t, cfg, "filter/keep_structured\":",
			"the existing logs/structured pipeline's filter/keep_structured processor must remain declared")
		assert.Contains(t, cfg, "awscloudwatchlogs/structured",
			"the existing awscloudwatchlogs/structured exporter must remain declared")
		assert.Contains(t, cfg, `"raw_log": true`,
			`the existing raw_log=true logs path must remain byte-unchanged`)
		assert.Contains(t, cfg, `"raw_log": false`,
			`the existing raw_log=false logs/structured path must remain byte-unchanged`)
	})

	// AdotTaskDefinitionCarriesConfigFingerprint asserts the ADOT config-change redeploy
	// trigger: the ADOT config is delivered by-reference (SSM SecureString read via the
	// container-def secrets valueFrom at task STARTUP), so a config-only change updates SSM
	// without altering the task definition and ECS never rolls the service -- the running
	// collector keeps serving the OLD config. The reference therefore embeds
	// sha256(adot_config_content) as the ADOT_CONFIG_SHA256 environment variable in every
	// container, so any config change produces a new task-definition revision and rolls the
	// service declaratively. This subtest asserts the echo output is a 64-hex sha256, then
	// follows the applied ECS chain (DescribeServices on the cluster/service outputs -> current
	// taskDefinition ARN -> DescribeTaskDefinition) and asserts a container's Environment
	// carries ADOT_CONFIG_SHA256 with exactly that value (DRY: one fingerprint source).
	t.Run("AdotTaskDefinitionCarriesConfigFingerprint", func(t *testing.T) {
		sha := terraform.Output(t, ctx.Terraform, "adot_config_sha256_echo")
		require.NotEmpty(t, sha,
			"adot_config_sha256_echo must be non-empty (the ADOT config fingerprint embedded in the task definition)")
		require.Regexp(t, "^[0-9a-f]{64}$", sha,
			"adot_config_sha256_echo must be a 64-char lowercase hex sha256, got: %s", sha)

		clusterName := terraform.Output(t, ctx.Terraform, "ecs_cluster_name")
		require.NotEmpty(t, clusterName,
			"ecs_cluster_name output must be non-empty to locate the ADOT service's task definition")
		serviceName := terraform.Output(t, ctx.Terraform, "ecs_service_name")
		require.NotEmpty(t, serviceName,
			"ecs_service_name output must be non-empty to locate the ADOT service's task definition")

		ctxBg := context.Background()
		cfg, err := config.LoadDefaultConfig(ctxBg, config.WithRegion(os.Getenv("AWS_DEFAULT_REGION")))
		require.NoError(t, err, "failed to load AWS SDK config")
		ecsClient := ecs.NewFromConfig(cfg)

		svcOut, err := ecsClient.DescribeServices(ctxBg, &ecs.DescribeServicesInput{
			Cluster:  aws.String(clusterName),
			Services: []string{serviceName},
		})
		require.NoError(t, err,
			"DescribeServices must succeed for cluster %s service %s", clusterName, serviceName)
		require.Len(t, svcOut.Services, 1,
			"exactly one ECS service named %s must exist on cluster %s", serviceName, clusterName)

		taskDefArn := aws.ToString(svcOut.Services[0].TaskDefinition)
		require.NotEmpty(t, taskDefArn,
			"the ADOT ECS service must reference a task definition")

		tdOut, err := ecsClient.DescribeTaskDefinition(ctxBg, &ecs.DescribeTaskDefinitionInput{
			TaskDefinition: aws.String(taskDefArn),
		})
		require.NoError(t, err, "DescribeTaskDefinition must succeed for %s", taskDefArn)
		require.NotNil(t, tdOut.TaskDefinition, "DescribeTaskDefinition must return a task definition")
		require.NotEmpty(t, tdOut.TaskDefinition.ContainerDefinitions,
			"the ADOT task definition must declare at least one container")

		fingerprintFound := false
		for _, containerDef := range tdOut.TaskDefinition.ContainerDefinitions {
			for _, envVar := range containerDef.Environment {
				if aws.ToString(envVar.Name) == "ADOT_CONFIG_SHA256" {
					fingerprintFound = true
					assert.Equal(t, sha, aws.ToString(envVar.Value),
						"container %s ADOT_CONFIG_SHA256 must equal adot_config_sha256_echo -- the task definition must "+
							"carry the CURRENT config fingerprint so a config-only change rolls the service",
						aws.ToString(containerDef.Name))
				}
			}
		}
		assert.True(t, fingerprintFound,
			"the applied ADOT task definition must embed the ADOT_CONFIG_SHA256 environment variable -- without it "+
				"a config-only SSM change never produces a new task-definition revision and ECS keeps serving the OLD config")
	})

	// AdotAutoscalingEnabledWithBaseline asserts the high-concurrency-ingestion autoscaling wiring
	// against the REAL applied AWS state (not just the rendered config): the ECS service's
	// DesiredCount equals the adot_desired_count baseline, and Application Auto Scaling was
	// actually created (aws_appautoscaling_target + aws_appautoscaling_policy, wired via
	// module.adot_service -> ecs-app-deploy -> ecs-service) with the min/max/cpu_target_percent
	// values from adot_autoscaling and a CPU target-tracking policy
	// (ECSServiceAverageCPUUtilization) -- proving desired_count (previously hardcoded 1) and
	// enable_autoscaling (previously hardcoded false) are genuinely wired end to end, not merely
	// present in the rendered HCL.
	t.Run("AdotAutoscalingEnabledWithBaseline", func(t *testing.T) {
		desiredCountEcho := terraform.Output(t, ctx.Terraform, "adot_desired_count_echo")
		require.NotEmpty(t, desiredCountEcho, "adot_desired_count_echo must be non-empty")
		wantDesiredCount, err := strconv.ParseInt(desiredCountEcho, 10, 32)
		require.NoError(t, err, "adot_desired_count_echo must be a valid integer, got: %s", desiredCountEcho)

		enableAutoscalingEcho := terraform.Output(t, ctx.Terraform, "adot_enable_autoscaling_echo")
		require.Equal(t, "true", enableAutoscalingEcho,
			"adot_enable_autoscaling must default to true (elastic headroom for the public high-concurrency ingestion edge)")

		autoscalingRaw := terraform.Output(t, ctx.Terraform, "adot_autoscaling_echo")
		require.NotEmpty(t, autoscalingRaw, "adot_autoscaling_echo must be non-empty")
		var wantAutoscaling struct {
			MinCapacity      int64   `json:"min_capacity"`
			MaxCapacity      int64   `json:"max_capacity"`
			CpuTargetPercent float64 `json:"cpu_target_percent"`
		}
		require.NoError(t, json.Unmarshal([]byte(autoscalingRaw), &wantAutoscaling),
			"adot_autoscaling_echo must be valid JSON, got: %s", autoscalingRaw)
		require.GreaterOrEqual(t, wantAutoscaling.MaxCapacity, wantAutoscaling.MinCapacity,
			"adot_autoscaling.max_capacity must be >= min_capacity (also enforced by the variable validation)")
		require.GreaterOrEqual(t, wantDesiredCount, wantAutoscaling.MinCapacity,
			"adot_desired_count must be >= adot_autoscaling.min_capacity (also enforced by the variable validation)")
		require.LessOrEqual(t, wantDesiredCount, wantAutoscaling.MaxCapacity,
			"adot_desired_count must be <= adot_autoscaling.max_capacity (also enforced by the variable validation)")

		clusterName := terraform.Output(t, ctx.Terraform, "ecs_cluster_name")
		require.NotEmpty(t, clusterName, "ecs_cluster_name output must be non-empty")
		serviceName := terraform.Output(t, ctx.Terraform, "ecs_service_name")
		require.NotEmpty(t, serviceName, "ecs_service_name output must be non-empty")

		ctxBg := context.Background()
		cfg, err := config.LoadDefaultConfig(ctxBg, config.WithRegion(os.Getenv("AWS_DEFAULT_REGION")))
		require.NoError(t, err, "failed to load AWS SDK config")

		// The ECS service's DesiredCount must equal the baseline: the PRIMARY capacity lever
		// (not scale-out) must carry the configured concurrent-load target from the first apply.
		ecsClient := ecs.NewFromConfig(cfg)
		svcOut, err := ecsClient.DescribeServices(ctxBg, &ecs.DescribeServicesInput{
			Cluster:  aws.String(clusterName),
			Services: []string{serviceName},
		})
		require.NoError(t, err, "DescribeServices must succeed for cluster %s service %s", clusterName, serviceName)
		require.Len(t, svcOut.Services, 1, "exactly one ECS service named %s must exist", serviceName)
		assert.EqualValues(t, wantDesiredCount, svcOut.Services[0].DesiredCount,
			"the applied ECS service DesiredCount must equal adot_desired_count (%d) -- desired_count is no longer hardcoded to 1", wantDesiredCount)

		// Application Auto Scaling must have been created for this service (the previously
		// hardcoded enable_autoscaling = false meant NEITHER of the resources below ever existed).
		resourceID := fmt.Sprintf("service/%s/%s", clusterName, serviceName)
		aasClient := applicationautoscaling.NewFromConfig(cfg)

		targetsOut, err := aasClient.DescribeScalableTargets(ctxBg, &applicationautoscaling.DescribeScalableTargetsInput{
			ServiceNamespace:  types.ServiceNamespaceEcs,
			ResourceIds:       []string{resourceID},
			ScalableDimension: types.ScalableDimensionECSServiceDesiredCount,
		})
		require.NoError(t, err, "DescribeScalableTargets must succeed for resource %s", resourceID)
		require.Len(t, targetsOut.ScalableTargets, 1,
			"exactly one aws_appautoscaling_target must exist for %s (adot_enable_autoscaling = true)", resourceID)
		target := targetsOut.ScalableTargets[0]
		assert.EqualValues(t, wantAutoscaling.MinCapacity, aws.ToInt32(target.MinCapacity),
			"the applied Application Auto Scaling target MinCapacity must equal adot_autoscaling.min_capacity (%d) -- the baseline must carry the full expected concurrent load", wantAutoscaling.MinCapacity)
		assert.EqualValues(t, wantAutoscaling.MaxCapacity, aws.ToInt32(target.MaxCapacity),
			"the applied Application Auto Scaling target MaxCapacity must equal adot_autoscaling.max_capacity (%d)", wantAutoscaling.MaxCapacity)

		policiesOut, err := aasClient.DescribeScalingPolicies(ctxBg, &applicationautoscaling.DescribeScalingPoliciesInput{
			ServiceNamespace:  types.ServiceNamespaceEcs,
			ResourceId:        aws.String(resourceID),
			ScalableDimension: types.ScalableDimensionECSServiceDesiredCount,
		})
		require.NoError(t, err, "DescribeScalingPolicies must succeed for resource %s", resourceID)
		require.Len(t, policiesOut.ScalingPolicies, 1,
			"exactly one aws_appautoscaling_policy must exist for %s", resourceID)
		policy := policiesOut.ScalingPolicies[0]
		require.NotNil(t, policy.TargetTrackingScalingPolicyConfiguration,
			"the scaling policy must be a TargetTrackingScaling policy (CPU target-tracking)")
		require.NotNil(t, policy.TargetTrackingScalingPolicyConfiguration.PredefinedMetricSpecification,
			"the target-tracking policy must use a predefined metric specification")
		assert.Equal(t, types.MetricTypeECSServiceAverageCPUUtilization,
			policy.TargetTrackingScalingPolicyConfiguration.PredefinedMetricSpecification.PredefinedMetricType,
			"the autoscaling policy must target ECSServiceAverageCPUUtilization (CPU target-tracking, not request-count -- ALBRequestCountPerTarget is deliberately deferred)")
		assert.InDelta(t, wantAutoscaling.CpuTargetPercent, aws.ToFloat64(policy.TargetTrackingScalingPolicyConfiguration.TargetValue), 0.01,
			"the autoscaling policy TargetValue must equal adot_autoscaling.cpu_target_percent (%v)", wantAutoscaling.CpuTargetPercent)
	})

	// TelemetryLogHopResources asserts the telemetry CloudWatch Logs ingest hop is provisioned:
	// a dedicated log group, a CWL-to-Firehose role, and a subscription filter whose destination
	// is the data-lake Firehose.
	t.Run("TelemetryLogHopResources", func(t *testing.T) {
		// The telemetry ingest hop owns exactly one log STREAM and one subscription FILTER
		// (no other resource of these types exists in the module tree, so a module-wide count
		// of 1 is correct and hop-specific). The dedicated telemetry ingest log GROUP is NOT
		// asserted by a module-wide aws_cloudwatch_log_group count: the composed module tree
		// legitimately creates THREE log groups (the telemetry-ingest group, the reused
		// waf-webacl's own aws-waf-logs-* group, and the ECS service's awslogs group), so a
		// count of 1 is wrong. The dedicated ingest group is instead proven below via its
		// telemetry_log_group_name / _arn outputs (name contains /ingest/otlp-logs).
		assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_stream", 1)
		assertions.AssertResourceCount(t, ctx, "aws_cloudwatch_log_subscription_filter", 1)

		logGroupName := terraform.Output(t, ctx.Terraform, "telemetry_log_group_name")
		assert.Contains(t, logGroupName, "/ingest/otlp-logs",
			"telemetry_log_group_name must be the dedicated telemetry ingest group")

		logGroupArn := terraform.Output(t, ctx.Terraform, "telemetry_log_group_arn")
		assert.True(t, strings.HasPrefix(logGroupArn, "arn:aws:logs:"),
			"telemetry_log_group_arn must be a CloudWatch Logs ARN, got: %s", logGroupArn)

		roleArn := terraform.Output(t, ctx.Terraform, "cwl_to_firehose_role_arn")
		assert.True(t, strings.HasPrefix(roleArn, "arn:aws:iam::"),
			"cwl_to_firehose_role_arn must be an IAM role ARN, got: %s", roleArn)

		// The match-all subscription filter must forward to the EXISTING data-lake Firehose
		// (the same stream supplied as firehose_delivery_stream_arn), not a new stream.
		destArn := terraform.Output(t, ctx.Terraform, "telemetry_subscription_filter_destination_arn")
		firehoseArn := terraform.Output(t, ctx.Terraform, "firehose_delivery_stream_arn_echo")
		assert.Equal(t, firehoseArn, destArn,
			"subscription filter destination must equal the firehose_delivery_stream_arn input (existing data-lake Firehose)")
	})

	// TerraformVersion asserts the pinned Terraform version constraint is satisfied.
	t.Run("TerraformVersion", func(t *testing.T) {
		assertions.AssertTerraformVersion(t, ctx, "1.15.5")
	})

	// Idempotency asserts a plan after apply exits with code 0 (no changes). Runs LAST
	// among the apply-dependent subtests so all read-only assertions observe the freshly
	// applied state before the parent t.Cleanup destroys it.
	t.Run("Idempotency", func(t *testing.T) {
		exitCode := terraform.PlanExitCode(t, ctx.Terraform)
		assert.Equal(t, 0, exitCode,
			"second plan must show zero resource changes (exit code 0 means no changes)")
	})
}

// ---------------------------------------------------------------------------
// Static source-inspection tests (no applied state). These read variables.tf /
// outputs.tf / main.tf on disk, so they remain standalone top-level tests rather
// than sub-tests of TestCollectorIngestionDefault.
// ---------------------------------------------------------------------------

// TestCollectorIngestionTelemetryLogHopInputsInVariablesTf asserts the variables.tf
// declares the telemetry CloudWatch Logs hop inputs.
func TestCollectorIngestionTelemetryLogHopInputsInVariablesTf(t *testing.T) {
	variablesContent, err := os.ReadFile("../../variables.tf")
	require.NoError(t, err, "variables.tf must exist and be readable")
	content := string(variablesContent)

	requiredInputs := []string{
		`variable "telemetry_log_kms_key_arn"`,
		`variable "telemetry_log_retention_in_days"`,
		`variable "cwl_to_firehose_role_name"`,
	}
	for _, input := range requiredInputs {
		assert.Contains(t, content, input,
			"variables.tf must declare %s for the telemetry CloudWatch Logs ingest hop", input)
	}
}

// TestCollectorIngestionSectionFiveTenInputsInVariablesTf asserts the variables.tf
// declares all mandatory docs/terragrunt-concepts.md inputs without placeholder values (AC-9).
func TestCollectorIngestionSectionFiveTenInputsInVariablesTf(t *testing.T) {
	variablesContent, err := os.ReadFile("../../variables.tf")
	require.NoError(t, err, "variables.tf must exist and be readable")
	content := string(variablesContent)

	requiredInputs := []string{
		`variable "adot_image"`,
		`variable "adot_task_cpu"`,
		`variable "adot_task_memory"`,
		`variable "adot_receiver_max_request_body_size"`,
		`variable "rate_limit_per_ip"`,
		`variable "vpc_cidr_block"`,
		`variable "subnet_layout"`,
		`variable "waf_log_kms_key_arn"`,
	}

	for _, input := range requiredInputs {
		assert.Contains(t, content, input,
			"variables.tf must declare %s per docs/terragrunt-concepts.md (AC-9)", input)
	}

	// Confirm no TODO placeholders in the file.
	assert.NotContains(t, content, "TODO",
		"variables.tf must not contain TODO placeholders -- all values must be concrete (AC-9, fail-fast)")

	// Confirm concrete default for adot_receiver_max_request_body_size (4194304 per D44).
	assert.Contains(t, content, "4194304",
		"adot_receiver_max_request_body_size must have concrete default 4194304 bytes per D44 (AC-9)")

	// Confirm concrete default for rate_limit_per_ip (2000 per D44).
	assert.Contains(t, content, "2000",
		"rate_limit_per_ip must have concrete default 2000 per D44 (AC-9)")
}

// TestCollectorIngestionD37InputsInVariablesTf asserts the variables.tf declares
// all six D37 inputs (AC-3, AC-9).
func TestCollectorIngestionD37InputsInVariablesTf(t *testing.T) {
	variablesContent, err := os.ReadFile("../../variables.tf")
	require.NoError(t, err, "variables.tf must exist and be readable")
	content := string(variablesContent)

	d37Inputs := []string{
		`variable "firehose_delivery_stream_arn"`,
		`variable "collector_service_fqdn"`,
		`variable "collector_pretty_fqdn"`,
		`variable "prod_hosted_zone_id"`,
		`variable "certificate_arn"`,
		`variable "domain_validation_options"`,
	}

	for _, input := range d37Inputs {
		assert.Contains(t, content, input,
			"variables.tf must declare %s as an input per D37 (AC-3)", input)
	}
}

// TestCollectorIngestionOutputsTfExportsFourOutputs asserts the outputs.tf exports
// cloudfront_domain_name, cloudfront_hosted_zone_id, alb_arn, and ecs_service_name (AC-1).
func TestCollectorIngestionOutputsTfExportsFourOutputs(t *testing.T) {
	outputsContent, err := os.ReadFile("../../outputs.tf")
	require.NoError(t, err, "outputs.tf must exist and be readable")
	content := string(outputsContent)

	requiredOutputs := []string{
		`output "cloudfront_domain_name"`,
		`output "cloudfront_hosted_zone_id"`,
		`output "alb_arn"`,
		`output "ecs_service_name"`,
	}

	for _, output := range requiredOutputs {
		assert.Contains(t, content, output,
			"outputs.tf must export %s (AC-1)", output)
	}

	// Confirm cloudfront outputs source from module.cloudfront.
	assert.Contains(t, content, "module.cloudfront.distribution_domain_name",
		"cloudfront_domain_name must source from module.cloudfront.distribution_domain_name (AC-1)")
	assert.Contains(t, content, "module.cloudfront.distribution_hosted_zone_id",
		"cloudfront_hosted_zone_id must source from module.cloudfront.distribution_hosted_zone_id (AC-1)")
}

// TestCollectorIngestionMainTfNoAcmCertificateBlock asserts main.tf contains
// aws_acm_certificate_validation but no aws_acm_certificate resource block (AC-9).
func TestCollectorIngestionMainTfNoAcmCertificateBlock(t *testing.T) {
	mainContent, err := os.ReadFile("../../main.tf")
	require.NoError(t, err, "main.tf must exist and be readable")
	content := string(mainContent)

	assert.Contains(t, content, "aws_acm_certificate_validation",
		"main.tf must contain aws_acm_certificate_validation resource (AC-9)")
	assert.NotContains(t, content, `resource "aws_acm_certificate"`,
		"main.tf must NOT contain aws_acm_certificate resource -- certs live in acm-collector only (AC-9)")
}

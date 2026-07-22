package default_test

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fixtureRoot returns the absolute path to the examples/default fixture directory,
// resolving relative to the location of this test file at runtime.
func fixtureRoot(t *testing.T) string {
	t.Helper()
	_, testFile, _, ok := runtime.Caller(0)
	require.True(t, ok, "runtime.Caller must succeed to locate the test file")
	// tests/default/collector_ingestion_test.go -> parent is tests/default/
	// -> parent is tests/ -> parent is the module root -> examples/default
	moduleRoot := filepath.Clean(filepath.Join(filepath.Dir(testFile), "..", ".."))
	return filepath.Join(moduleRoot, "examples", "default")
}

// TestFixtureSandboxDomainHasNoInternalDefault asserts that examples/default/variables.tf
// does NOT default sandbox_domain to a reserved .internal TLD (AC-FIX-T16-2).
// A .internal domain is never publicly resolvable and cannot be NS-delegated, so ACM
// DNS validation cannot complete -- the apply hangs and orphans resources (AC-FIX-T16-1).
func TestFixtureSandboxDomainHasNoInternalDefault(t *testing.T) {
	varsContent, err := os.ReadFile(filepath.Join(fixtureRoot(t), "variables.tf"))
	require.NoError(t, err, "examples/default/variables.tf must be readable")
	content := string(varsContent)

	assert.NotContains(t, content, ".internal",
		"sandbox_domain must not default to a reserved .internal TLD; "+
			"the .internal domain is never publicly resolvable and blocks ACM DNS validation (AC-FIX-T16-2)")
}

// TestFixtureSandboxDomainHasNoHardcodedDefault asserts that examples/default/variables.tf
// does NOT provide a default for sandbox_domain at all (AC-FIX-T16-2).
// A real NS-delegatable domain must be operator-supplied via TF_VAR_sandbox_domain
// (12-factor config, fail-fast on unset input).
func TestFixtureSandboxDomainHasNoHardcodedDefault(t *testing.T) {
	varsContent, err := os.ReadFile(filepath.Join(fixtureRoot(t), "variables.tf"))
	require.NoError(t, err, "examples/default/variables.tf must be readable")
	content := string(varsContent)

	// Locate the sandbox_domain variable block.
	sandboxDomainIdx := strings.Index(content, `variable "sandbox_domain"`)
	require.NotEqual(t, -1, sandboxDomainIdx,
		"variables.tf must still declare the sandbox_domain variable")

	// Extract the variable block (up to the next top-level variable/resource/locals block).
	blockContent := content[sandboxDomainIdx:]
	// Find the closing brace of the variable block.
	braceDepth := 0
	blockEnd := len(blockContent)
	for i, ch := range blockContent {
		if ch == '{' {
			braceDepth++
		} else if ch == '}' {
			braceDepth--
			if braceDepth == 0 {
				blockEnd = i + 1
				break
			}
		}
	}
	sandboxBlock := blockContent[:blockEnd]

	// Use a whitespace-tolerant regex to detect any HCL default assignment in the block.
	// terraform fmt aligns the = operator with multiple spaces (e.g. "default     = value"),
	// so a literal single-space check ("default =") never matches a fmt-compliant file.
	// The pattern (?m)^\s*default\s*= matches any leading whitespace and any number of
	// spaces between "default" and "=", covering all terraform fmt output variants.
	defaultAssignmentRe := regexp.MustCompile(`(?m)^\s*default\s*=`)
	assert.Equal(t, "", defaultAssignmentRe.FindString(sandboxBlock),
		"sandbox_domain variable must not have a default = assignment (including fmt-aligned forms "+
			"such as 'default     = \"value\"') -- it must be supplied explicitly via "+
			"TF_VAR_sandbox_domain pointing to a real NS-delegatable domain (12-factor, AC-FIX-T16-2)")
}

// TestFixtureTfvarsHasNoSandboxDomain asserts that examples/default/terraform.tfvars
// does NOT hardcode a sandbox_domain value (AC-FIX-T16-2, 12-factor config).
// The domain must come from TF_VAR_sandbox_domain at runtime, not from a committed tfvars.
func TestFixtureTfvarsHasNoSandboxDomain(t *testing.T) {
	tfvarsContent, err := os.ReadFile(filepath.Join(fixtureRoot(t), "terraform.tfvars"))
	require.NoError(t, err, "examples/default/terraform.tfvars must be readable")
	content := string(tfvarsContent)

	assert.NotContains(t, content, "sandbox_domain",
		"terraform.tfvars must not hardcode sandbox_domain -- "+
			"supply via TF_VAR_sandbox_domain from the operator environment (12-factor, AC-FIX-T16-2)")
}

// TestFixtureMainTfPreservesAcmWiring asserts that examples/default/main.tf still
// creates an aws_acm_certificate and wires certificate_arn to the module call (AC-FIX-T16-4).
// The custom-alias CloudFront configuration must remain intact; security is not weakened.
func TestFixtureMainTfPreservesAcmWiring(t *testing.T) {
	mainContent, err := os.ReadFile(filepath.Join(fixtureRoot(t), "main.tf"))
	require.NoError(t, err, "examples/default/main.tf must be readable")
	content := string(mainContent)

	assert.Contains(t, content, `resource "aws_acm_certificate"`,
		"fixture main.tf must still create an ACM certificate for DNS validation (AC-FIX-T16-4)")
	assert.Contains(t, content, "certificate_arn",
		"fixture main.tf must still wire certificate_arn to the module call (AC-FIX-T16-4)")
	assert.Contains(t, content, `resource "aws_route53_zone"`,
		"fixture main.tf must still create the Route53 hosted zone for ACM DNS validation (AC-FIX-T16-4)")
	assert.Contains(t, content, `resource "aws_route53_record"`,
		"fixture main.tf must still create validation DNS records for ACM cert (AC-FIX-T16-4)")
	assert.NotContains(t, content, "sandbox-telemetry-test.internal",
		"fixture main.tf must not hardcode the .internal test domain literal (AC-FIX-T16-2, AC-FIX-T16-4)")
}

// TestFixtureNoEmDash asserts that no em-dash character (U+2014) is present in any
// touched fixture file (AC-FIX-T16-4).
func TestFixtureNoEmDash(t *testing.T) {
	emDash := string([]byte{0xe2, 0x80, 0x94}) // UTF-8 encoding of U+2014

	files := []string{
		filepath.Join(fixtureRoot(t), "main.tf"),
		filepath.Join(fixtureRoot(t), "variables.tf"),
		filepath.Join(fixtureRoot(t), "terraform.tfvars"),
	}

	for _, f := range files {
		content, err := os.ReadFile(f)
		require.NoError(t, err, "must be able to read %s", f)
		assert.NotContains(t, string(content), emDash,
			"file %s must not contain em-dash (U+2014); use -- instead (AC-FIX-T16-4)", f)
	}
}

// TestFixtureSandboxDomainDerivedLocals asserts that examples/default/main.tf derives
// the collector_pretty_fqdn and collector_service_fqdn locals from var.sandbox_domain,
// not from any hardcoded domain literal (12-factor config, AC-FIX-T16-2).
func TestFixtureSandboxDomainDerivedLocals(t *testing.T) {
	mainContent, err := os.ReadFile(filepath.Join(fixtureRoot(t), "main.tf"))
	require.NoError(t, err, "examples/default/main.tf must be readable")
	content := string(mainContent)

	assert.Contains(t, content, "var.sandbox_domain",
		"examples/default/main.tf must derive domain names from var.sandbox_domain "+
			"(input-driven, 12-factor config, AC-FIX-T16-2)")
}

// moduleRoot returns the absolute path to the collector-ingestion reference module root,
// resolving relative to the location of this test file at runtime.
func moduleRoot(t *testing.T) string {
	t.Helper()
	_, testFile, _, ok := runtime.Caller(0)
	require.True(t, ok, "runtime.Caller must succeed to locate the test file")
	// tests/default/collector_ingestion_test.go -> parent is tests/default/
	// -> parent is tests/ -> parent is the module root.
	return filepath.Clean(filepath.Join(filepath.Dir(testFile), "..", ".."))
}

// TestPublicDnsRecordToggleDeclared asserts that variables.tf declares the
// create_public_dns_record toggle defaulting to true so the standalone example/terratest
// fixture (self-contained: it creates its own Route53 zone) keeps owning and provisioning
// the public collector record. Single-owner DNS fix (AC-8/D39).
func TestPublicDnsRecordToggleDeclared(t *testing.T) {
	varsContent, err := os.ReadFile(filepath.Join(moduleRoot(t), "variables.tf"))
	require.NoError(t, err, "module variables.tf must be readable")
	content := string(varsContent)

	idx := strings.Index(content, `variable "create_public_dns_record"`)
	require.NotEqual(t, -1, idx,
		"variables.tf must declare the create_public_dns_record toggle (single-owner DNS, AC-8/D39)")

	// Extract the variable block.
	block := content[idx:]
	braceDepth := 0
	blockEnd := len(block)
	for i, ch := range block {
		if ch == '{' {
			braceDepth++
		} else if ch == '}' {
			braceDepth--
			if braceDepth == 0 {
				blockEnd = i + 1
				break
			}
		}
	}
	varBlock := block[:blockEnd]

	assert.Contains(t, varBlock, "type        = bool",
		"create_public_dns_record must be a bool toggle")
	defaultTrueRe := regexp.MustCompile(`(?m)^\s*default\s*=\s*true\b`)
	assert.Regexp(t, defaultTrueRe, varBlock,
		"create_public_dns_record must default to true so the standalone example/terratest "+
			"fixture keeps creating the public record unchanged (self-contained, AC-8/D39)")
}

// TestPublicDnsRecordGuardedByToggle asserts that main.tf gates the public route53_record
// module on the create_public_dns_record toggle via count, so when false (live leaf) the
// reference creates exactly zero public records and the dedicated dns-collector unit is the
// single owner (AC-8/D39). The aws_acm_certificate_validation waiter must be unconditional
// so the collector always consumes an ISSUED cert.
func TestPublicDnsRecordGuardedByToggle(t *testing.T) {
	mainContent, err := os.ReadFile(filepath.Join(moduleRoot(t), "main.tf"))
	require.NoError(t, err, "module main.tf must be readable")
	content := string(mainContent)

	// Locate the route53_record module block and assert it is count-gated on the toggle.
	idx := strings.Index(content, `module "route53_record"`)
	require.NotEqual(t, -1, idx, "main.tf must declare the route53_record module")
	block := content[idx:]
	braceDepth := 0
	blockEnd := len(block)
	for i, ch := range block {
		if ch == '{' {
			braceDepth++
		} else if ch == '}' {
			braceDepth--
			if braceDepth == 0 {
				blockEnd = i + 1
				break
			}
		}
	}
	recordBlock := block[:blockEnd]
	countGuardRe := regexp.MustCompile(`count\s*=\s*var\.create_public_dns_record\s*\?\s*1\s*:\s*0`)
	assert.Regexp(t, countGuardRe, recordBlock,
		"route53_record module must be gated by 'count = var.create_public_dns_record ? 1 : 0' "+
			"so the live leaf creates zero public records (single owner = dns-collector, AC-8/D39)")

	// The cert-validation waiter must remain present and unconditional (creates no DNS record,
	// so the collector still resolves an ISSUED cert in both deployment modes).
	assert.Contains(t, content, `resource "aws_acm_certificate_validation" "collector"`,
		"main.tf must keep the aws_acm_certificate_validation waiter so the collector consumes an ISSUED cert")
	assert.NotContains(t, content,
		`resource "aws_acm_certificate_validation" "collector" {`+"\n"+`  count`,
		"the aws_acm_certificate_validation waiter must NOT be toggled off (it creates no DNS record)")
}

// TestEnvcommonSetsPublicDnsRecordFalse asserts that the live shared template
// _envcommon/collector-ingestion.hcl sets create_public_dns_record = false so every live
// sandbox and prod collector leaf drops the conflicting public record while the dedicated
// dns-collector unit owns it (prod parity via the shared template, AC-8/D39).
func TestEnvcommonSetsPublicDnsRecordFalse(t *testing.T) {
	// Walk up from the module root to the repository root, then to the shared template.
	repoRoot := filepath.Clean(filepath.Join(moduleRoot(t), "..", "..", "..", ".."))
	envcommon := filepath.Join(repoRoot, "terragrunt", "_envcommon", "collector-ingestion.hcl")
	content, err := os.ReadFile(envcommon)
	require.NoError(t, err,
		"terragrunt/_envcommon/collector-ingestion.hcl must be readable at %s", envcommon)

	falseAssignRe := regexp.MustCompile(`(?m)^\s*create_public_dns_record\s*=\s*false\b`)
	assert.Regexp(t, falseAssignRe, string(content),
		"_envcommon/collector-ingestion.hcl must set create_public_dns_record = false so every "+
			"live sandbox and prod leaf drops the public CNAME and dns-collector is the single "+
			"owner of the A-alias R4 (prod parity, AC-8/D39)")
}

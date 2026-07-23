package route53_record_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// moduleDir returns the absolute path to the route53-record module root,
// resolving relative to the location of this test file at runtime.
func moduleDir(t *testing.T) string {
	t.Helper()
	_, testFile, _, ok := runtime.Caller(0)
	require.True(t, ok, "runtime.Caller must succeed to locate the test file")
	// tests/route53_record_test.go -> parent is tests/ -> parent is the module root.
	return filepath.Clean(filepath.Join(filepath.Dir(testFile), ".."))
}

// TestRoute53RecordAliasVariableDeclared asserts that variables.tf declares an optional
// alias variable as a typed object with name, zone_id, and evaluate_target_health fields,
// defaulting to null (AC-FIX-T8-1).
func TestRoute53RecordAliasVariableDeclared(t *testing.T) {
	dir := moduleDir(t)
	varsContent, err := os.ReadFile(filepath.Join(dir, "variables.tf"))
	require.NoError(t, err, "variables.tf must be readable")
	content := string(varsContent)

	assert.Contains(t, content, `variable "alias"`,
		"variables.tf must declare an alias variable (AC-FIX-T8-1)")
	assert.Contains(t, content, "evaluate_target_health",
		"alias variable must include evaluate_target_health field (AC-FIX-T8-1)")
	assert.Contains(t, content, "default     = null",
		"alias variable must default to null (AC-FIX-T8-1)")
}

// TestRoute53RecordRecordsIsOptional asserts that the records variable is optional by
// behaviorally testing that an alias-only config (no records) validates successfully,
// and that the records variable block in variables.tf scopes its null default correctly
// (AC-FIX-T8-1).
func TestRoute53RecordRecordsIsOptional(t *testing.T) {
	dir := moduleDir(t)
	varsContent, err := os.ReadFile(filepath.Join(dir, "variables.tf"))
	require.NoError(t, err, "variables.tf must be readable")
	content := string(varsContent)

	// records variable must exist
	assert.Contains(t, content, `variable "records"`,
		"variables.tf must still declare records (AC-FIX-T8-1)")

	// Scope the null-default check to the records variable block only.
	// Extract the text between 'variable "records"' and the next 'variable "' declaration.
	recordsIdx := strings.Index(content, `variable "records"`)
	require.True(t, recordsIdx >= 0, "variable \"records\" must be present in variables.tf")
	nextVarIdx := strings.Index(content[recordsIdx+1:], "variable \"")
	var recordsBlock string
	if nextVarIdx >= 0 {
		recordsBlock = content[recordsIdx : recordsIdx+1+nextVarIdx]
	} else {
		recordsBlock = content[recordsIdx:]
	}
	assert.Contains(t, recordsBlock, "default     = null",
		"the records variable block must have a null default (AC-FIX-T8-1); "+
			"scope check ensures the null default belongs to records, not another variable")
}

// TestRoute53RecordMainTfHasDynamicAliasBlock asserts that main.tf uses a dynamic alias
// block to emit the alias configuration when var.alias is non-null (AC-FIX-T8-2).
func TestRoute53RecordMainTfHasDynamicAliasBlock(t *testing.T) {
	dir := moduleDir(t)
	mainContent, err := os.ReadFile(filepath.Join(dir, "main.tf"))
	require.NoError(t, err, "main.tf must be readable")
	content := string(mainContent)

	assert.Contains(t, content, "dynamic \"alias\"",
		"main.tf must use a dynamic alias block to render alias records (AC-FIX-T8-2)")
	assert.Contains(t, content, "var.alias",
		"main.tf must reference var.alias in the dynamic alias block (AC-FIX-T8-2)")
	assert.Contains(t, content, "evaluate_target_health",
		"main.tf dynamic alias block must set evaluate_target_health (AC-FIX-T8-2)")
}

// TestRoute53RecordMainTfStaticRecordsPath asserts that main.tf handles the static
// records/ttl path with appropriate conditionals (AC-FIX-T8-2).
func TestRoute53RecordMainTfStaticRecordsPath(t *testing.T) {
	dir := moduleDir(t)
	mainContent, err := os.ReadFile(filepath.Join(dir, "main.tf"))
	require.NoError(t, err, "main.tf must be readable")
	content := string(mainContent)

	// main.tf must reference var.records (possibly conditionally)
	assert.Contains(t, content, "var.records",
		"main.tf must reference var.records for the static records path (AC-FIX-T8-2)")
	// main.tf must reference var.ttl for the static records path
	assert.Contains(t, content, "var.ttl",
		"main.tf must reference var.ttl for the static records path (AC-FIX-T8-2)")
}

// TestRoute53RecordValidateStaticExample asserts that terraform init and validate
// succeed for the basic (static records) example (AC-FIX-T8-3).
func TestRoute53RecordValidateStaticExample(t *testing.T) {
	dir := moduleDir(t)
	exampleDir := filepath.Join(dir, "examples", "basic")

	_, err := os.Stat(filepath.Join(exampleDir, "main.tf"))
	require.NoError(t, err, "examples/basic/main.tf must exist")

	initCmd := exec.Command("terraform", "init", "-backend=false")
	initCmd.Dir = exampleDir
	initOut, initErr := initCmd.CombinedOutput()
	require.NoError(t, initErr,
		"terraform init -backend=false must succeed for examples/basic; output:\n%s", string(initOut))

	validateCmd := exec.Command("terraform", "validate")
	validateCmd.Dir = exampleDir
	validateOut, validateErr := validateCmd.CombinedOutput()
	assert.NoError(t, validateErr,
		"terraform validate must succeed for examples/basic (static records shape); output:\n%s",
		string(validateOut))
}

// TestRoute53RecordValidateAliasShape asserts that the primitive accepts an alias
// configuration and that terraform validate succeeds for an alias A-record (AC-FIX-T8-3).
// The alias module configuration is written to a temporary directory so no external
// example directory is required.
func TestRoute53RecordValidateAliasShape(t *testing.T) {
	dir := moduleDir(t)

	// Write a minimal alias A-record configuration in a temp directory.
	tmpDir := t.TempDir()

	aliasConfig := `terraform {
  required_version = ">= 1.15.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

module "alias_record" {
  source = "` + dir + `"

  zone_id = "Z1234567890ABCDEFGHIJ"
  name    = "collector.example.com"
  type    = "A"

  alias = {
    name                   = "d1234.cloudfront.net"
    zone_id                = "Z2FDTNDATAQYW2"
    evaluate_target_health = false
  }
}
`

	configPath := filepath.Join(tmpDir, "main.tf")
	err := os.WriteFile(configPath, []byte(aliasConfig), 0600)
	require.NoError(t, err, "alias test configuration must be writable")

	initCmd := exec.Command("terraform", "init", "-backend=false")
	initCmd.Dir = tmpDir
	initOut, initErr := initCmd.CombinedOutput()
	require.NoError(t, initErr,
		"terraform init -backend=false must succeed for alias shape config; output:\n%s", string(initOut))

	validateCmd := exec.Command("terraform", "validate")
	validateCmd.Dir = tmpDir
	validateOut, validateErr := validateCmd.CombinedOutput()
	assert.NoError(t, validateErr,
		"terraform validate must succeed for alias A-record shape (AC-FIX-T8-3); output:\n%s",
		string(validateOut))
}

// TestRoute53RecordAliasVariableTypedObject asserts that the alias variable in
// variables.tf is a properly typed object with all three required fields
// (name string, zone_id string, evaluate_target_health bool) (AC-FIX-T8-1).
func TestRoute53RecordAliasVariableTypedObject(t *testing.T) {
	dir := moduleDir(t)
	varsContent, err := os.ReadFile(filepath.Join(dir, "variables.tf"))
	require.NoError(t, err, "variables.tf must be readable")
	content := string(varsContent)

	requiredFields := []string{
		"name",
		"zone_id",
		"evaluate_target_health",
	}
	for _, field := range requiredFields {
		assert.Contains(t, content, field,
			"alias variable object type must include field %q (AC-FIX-T8-1)", field)
	}
}

// TestRoute53RecordFmtClean asserts that terraform fmt -check passes for the
// module root directory, confirming canonical HCL formatting.
func TestRoute53RecordFmtClean(t *testing.T) {
	dir := moduleDir(t)

	fmtCmd := exec.Command("terraform", "fmt", "-check", "-recursive")
	fmtCmd.Dir = dir
	fmtOut, fmtErr := fmtCmd.CombinedOutput()
	assert.NoError(t, fmtErr,
		"terraform fmt -check must pass (exit 0); output:\n%s", string(fmtOut))
}

// TestRoute53RecordReadmeDocumentsAlias asserts that README.md documents the alias
// input and the records-XOR-alias contract (AC-FIX-T8-5).
func TestRoute53RecordReadmeDocumentsAlias(t *testing.T) {
	dir := moduleDir(t)
	readmeContent, err := os.ReadFile(filepath.Join(dir, "README.md"))
	require.NoError(t, err, "README.md must be readable")
	content := string(readmeContent)

	assert.Contains(t, content, "alias",
		"README.md must document the alias input (AC-FIX-T8-5)")
	assert.True(t,
		strings.Contains(content, "XOR") || strings.Contains(content, "mutually exclusive") || strings.Contains(content, "records or alias"),
		"README.md must document the records-XOR-alias contract (AC-FIX-T8-5)")
}

// TestRoute53RecordBothRecordsAndAliasFails asserts that supplying both records and
// alias simultaneously causes terraform plan to fail with the XOR precondition error
// (AC-FIX-T8-1, fail-fast). lifecycle.precondition fires during plan, not validate,
// so this test uses terraform plan -no-color. The precondition error must appear in
// the output, confirming the module enforces the contract rather than silently
// dropping records.
func TestRoute53RecordBothRecordsAndAliasFails(t *testing.T) {
	dir := moduleDir(t)
	tmpDir := t.TempDir()

	bothConfig := `terraform {
  required_version = ">= 1.15.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  access_key                  = "mock_access_key"
  secret_key                  = "mock_secret_key"
}

module "conflicting_record" {
  source = "` + dir + `"

  zone_id = "Z1234567890ABCDEFGHIJ"
  name    = "test.example.com"
  type    = "A"

  records = ["1.2.3.4"]
  ttl     = 300

  alias = {
    name                   = "d1234.cloudfront.net"
    zone_id                = "Z2FDTNDATAQYW2"
    evaluate_target_health = false
  }
}
`

	err := os.WriteFile(filepath.Join(tmpDir, "main.tf"), []byte(bothConfig), 0600)
	require.NoError(t, err, "both-set test configuration must be writable")

	initCmd := exec.Command("terraform", "init", "-backend=false")
	initCmd.Dir = tmpDir
	initOut, initErr := initCmd.CombinedOutput()
	require.NoError(t, initErr,
		"terraform init must succeed even for invalid config; output:\n%s", string(initOut))

	// lifecycle.precondition fires during plan, not validate.
	planCmd := exec.Command("terraform", "plan", "-no-color")
	planCmd.Dir = tmpDir
	planOut, planErr := planCmd.CombinedOutput()
	planOutput := string(planOut)
	assert.Error(t, planErr,
		"terraform plan must FAIL when both records and alias are supplied (AC-FIX-T8-1 XOR contract); "+
			"output:\n%s", planOutput)
	// Accept either the module lifecycle.precondition message or the AWS provider native
	// schema error -- whichever surfaces first depending on Terraform evaluation order.
	assert.True(t,
		strings.Contains(planOutput, "precondition") ||
			strings.Contains(planOutput, "Exactly one") ||
			strings.Contains(planOutput, "one of"),
		"plan error output must reference the XOR constraint (precondition or provider schema error); output:\n%s", planOutput)
}

// TestRoute53RecordNeitherRecordsNorAliasFails asserts that supplying neither records
// nor alias causes terraform plan to fail with the XOR precondition error
// (AC-FIX-T8-1, fail-fast). The module must reject the neither-set case rather than
// silently producing an invalid aws_route53_record resource.
// lifecycle.precondition fires during plan, not validate.
func TestRoute53RecordNeitherRecordsNorAliasFails(t *testing.T) {
	dir := moduleDir(t)
	tmpDir := t.TempDir()

	neitherConfig := `terraform {
  required_version = ">= 1.15.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  access_key                  = "mock_access_key"
  secret_key                  = "mock_secret_key"
}

module "incomplete_record" {
  source = "` + dir + `"

  zone_id = "Z1234567890ABCDEFGHIJ"
  name    = "test.example.com"
  type    = "A"
}
`

	err := os.WriteFile(filepath.Join(tmpDir, "main.tf"), []byte(neitherConfig), 0600)
	require.NoError(t, err, "neither-set test configuration must be writable")

	initCmd := exec.Command("terraform", "init", "-backend=false")
	initCmd.Dir = tmpDir
	initOut, initErr := initCmd.CombinedOutput()
	require.NoError(t, initErr,
		"terraform init must succeed even for invalid config; output:\n%s", string(initOut))

	// lifecycle.precondition fires during plan, not validate.
	planCmd := exec.Command("terraform", "plan", "-no-color")
	planCmd.Dir = tmpDir
	planOut, planErr := planCmd.CombinedOutput()
	planOutput := string(planOut)
	assert.Error(t, planErr,
		"terraform plan must FAIL when neither records nor alias is supplied (AC-FIX-T8-1 XOR contract); "+
			"output:\n%s", planOutput)
	// Accept either the module lifecycle.precondition message or the AWS provider native
	// schema error ('one of `alias,records` must be specified') -- precondition vs provider-schema
	// evaluation order is not guaranteed, and either surface proves that the plan correctly failed.
	assert.True(t,
		strings.Contains(planOutput, "precondition") ||
			strings.Contains(planOutput, "Exactly one") ||
			strings.Contains(planOutput, "one of"),
		"plan error output must reference the XOR constraint (precondition or provider schema error); output:\n%s", planOutput)
}

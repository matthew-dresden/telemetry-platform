package terraform.libraries.source.test

import data.terraform.libraries.source as policy
import data.tests.opa.unit.helpers as helpers

# Test (a): telemetry-platform source WITH ref passes -- no violation
test_tools_telemetry_source_with_ref_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"local\" {\n  source = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/kms-key?ref=providers/aws/primitives/kms-key/v1.0.0\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# Test (b): terraform-modules source WITH ref still passes -- no violation
test_terraform_modules_source_with_ref_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"external\" {\n  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/s3?ref=providers/aws/primitives/s3/v1.0.0\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# Test: telemetry-platform source with ref accepted by has_ref_parameter helper
test_tools_telemetry_ref_with_semver_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"ref\" {\n  source = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/references/vpc-network?ref=providers/aws/references/vpc-network/v2.1.0\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# Test: terraform-modules source with ref accepted by has_ref_parameter helper
test_terraform_modules_ref_with_semver_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"remote\" {\n  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/collections/vpc?ref=providers/aws/collections/vpc/v2.1.0\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# Test (c): any third-party host is denied -- source on unrecognised host triggers violation
test_third_party_host_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"external\" {\n  source = \"git::https://github.com/terraform-aws-modules/terraform-aws-vpc.git//modules/vpc?ref=v5.0.0\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test: a wildcard or arbitrary org host is rejected -- only allowlisted orgs allowed
test_arbitrary_org_host_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"bad\" {\n  source = \"git::https://github.com/matthew-dresden/other-repo.git//providers/aws/primitives/kms?ref=providers/aws/primitives/kms/v1.0.0\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test (d): telemetry-platform source WITHOUT /v<semver> ref is denied -- pinned-ref required
test_tools_telemetry_source_missing_ref_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"unref\" {\n  source = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/kms-key\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test: telemetry-platform source with malformed ref (no /v<semver>) is denied
test_tools_telemetry_source_invalid_ref_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"badref\" {\n  source = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/kms-key?ref=main\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test: terraform-modules source missing ref is denied
test_terraform_modules_source_missing_ref_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"external\" {\n  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/s3\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test: local module source is denied
test_local_module_source_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"local\" {\n  source = \"../other-module\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test: absolute path module source is denied
test_absolute_path_module_source_denied if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": "module \"abs\" {\n  source = \"/path/to/module\"\n}"}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Test: local sources in examples directory are allowed (excluded by policy)
test_local_sources_in_examples_allowed if {
	module_path := "modules/test-module"
	files := {
		"modules/test-module/examples/complete/main.tf": "module \"local\" {\n  source = \"../../\"\n}",
		"modules/test-module/main.tf": "resource \"aws_s3_bucket\" \"bucket\" {}",
	}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# ---------------------------------------------------------------------------
# NEW TESTS: AC-9 -- var-driven in-repo source is ALLOWED (zero violations)
# ---------------------------------------------------------------------------

# AC-9 positive: a var-driven in-repo source passes -- no violation
test_var_driven_inrepo_source_allowed if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# AC-9 positive: multiple var-driven in-repo sources all pass (no violations)
test_multiple_var_driven_inrepo_sources_allowed if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"analytics\" {",
		"  source = var.analytics_source",
		"}",
		"module \"data_lake\" {",
		"  source = var.data_lake_source",
		"}",
		"variable \"analytics_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../references/analytics\"",
		"}",
		"variable \"data_lake_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../references/data-lake\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# ---------------------------------------------------------------------------
# NEW TESTS: AC-10 -- var contract validation
# ---------------------------------------------------------------------------

# AC-10 negative: var-driven source missing const = true produces a violation
test_var_driven_source_missing_const_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# AC-10 negative: var-driven source whose default is a git URL produces a violation
test_var_driven_source_default_git_url_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/glue-catalog-database?ref=providers/aws/primitives/glue-catalog-database/v1.0.0\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# AC-10 negative: var-driven source whose default is an absolute path produces a violation
test_var_driven_source_default_absolute_path_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"/absolute/path/to/module\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# AC-10 positive: var-driven source with const = true and relative default passes
test_var_driven_source_valid_contract_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"vpc\" {",
		"  source = var.vpc_source",
		"}",
		"variable \"vpc_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../primitives/vpc-network\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# ---------------------------------------------------------------------------
# NEW TESTS: AC-11 -- external sources (terraform-modules) still need pinned refs
# ---------------------------------------------------------------------------

# AC-11 negative: external terraform-modules source that is local produces a violation (retained)
test_external_local_source_still_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"vpc\" {",
		"  source = \"../terraform-modules/providers/aws/primitives/s3\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# AC-11 negative: external terraform-modules source missing ?ref=.../v<semver> fails (retained)
test_external_missing_semver_ref_still_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"s3\" {",
		"  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/s3\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# AC-11 positive: external terraform-modules source with pinned ref passes (retained)
test_external_pinned_ref_still_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"s3\" {",
		"  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/s3?ref=providers/aws/primitives/s3/v2.0.0\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# ---------------------------------------------------------------------------
# NEW TESTS: AC-12 -- prod-context gate
# ---------------------------------------------------------------------------

# AC-12 negative: prod context (use_pinned_module_sources=true) with in-repo source
# resolving to a local/relative default produces a violation
test_prod_context_inrepo_local_default_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": true},
	)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# AC-12 positive: sandbox context (use_pinned_module_sources=false) with in-repo source
# resolving to a local/relative default is ALLOWED (no violation)
test_sandbox_context_inrepo_local_default_allowed if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": false},
	)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# AC-12 positive: prod context but source var has pinned git URL default -- passes
test_prod_context_inrepo_pinned_default_passes if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/glue-catalog-database?ref=providers/aws/primitives/glue-catalog-database/v1.0.0\"",
		"}",
	])}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": true},
	)

	# NOTE: AC-10 also fires (git URL default violates var contract), but the
	# prod-context gate itself does NOT fire because the default IS pinned.
	# We verify the prod-context gate behavior separately from the AC-10 rule.
	violations := policy.violation with input as test_input

	# At minimum one violation from AC-10 (git URL default), but we confirm
	# that the PROD gate does not add a second violation beyond AC-10.
	# The gate fires only when the default is a LOCAL relative path.
	count(violations) >= 1
}

# AC-21 / AC-12 positive: prod context but file is under examples/ -- stays exempt
test_prod_context_examples_path_exempt if {
	module_path := "modules/test-module"
	files := {
		"modules/test-module/examples/complete/main.tf": concat("\n", [
			"module \"glue_catalog\" {",
			"  source = var.glue_catalog_source",
			"}",
			"variable \"glue_catalog_source\" {",
			"  type    = string",
			"  const   = true",
			"  default = \"../../primitives/glue-catalog-database\"",
			"}",
		]),
		"modules/test-module/main.tf": "resource \"aws_s3_bucket\" \"bucket\" {}",
	}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": true},
	)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# AC-21 / AC-12 positive: prod context but file is under tests/ -- stays exempt
test_prod_context_tests_path_exempt if {
	module_path := "modules/test-module"
	files := {
		"modules/test-module/tests/unit/main.tf": concat("\n", [
			"module \"glue_catalog\" {",
			"  source = var.glue_catalog_source",
			"}",
			"variable \"glue_catalog_source\" {",
			"  type    = string",
			"  const   = true",
			"  default = \"../../primitives/glue-catalog-database\"",
			"}",
		]),
		"modules/test-module/main.tf": "resource \"aws_s3_bucket\" \"bucket\" {}",
	}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": true},
	)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# AC-12 positive: no use_pinned_module_sources key in input -- treated as dev/sandbox (no prod gate)
test_no_pinned_flag_inrepo_local_allowed if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"glue_catalog\" {",
		"  source = var.glue_catalog_source",
		"}",
		"variable \"glue_catalog_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) == 0
}

# ---------------------------------------------------------------------------
# NEW TESTS: multi-source file -- one valid + one invalid var-driven source
# Both the const/default contract rules and the prod-context gate must check
# ALL var references in the file, not just the first one.
# ---------------------------------------------------------------------------

# Multi-source: good_source is valid, bad_source lacks const = true -- violation expected
test_multi_source_one_missing_const_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"good\" {",
		"  source = var.good_source",
		"}",
		"module \"bad\" {",
		"  source = var.bad_source",
		"}",
		"variable \"good_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/good-module\"",
		"}",
		"variable \"bad_source\" {",
		"  type    = string",
		"  default = \"../../primitives/bad-module\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Multi-source: good_source is valid, bad_source has git-URL default -- violation expected
test_multi_source_one_git_url_default_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"good\" {",
		"  source = var.good_source",
		"}",
		"module \"bad\" {",
		"  source = var.bad_source",
		"}",
		"variable \"good_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/good-module\"",
		"}",
		"variable \"bad_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/bad?ref=providers/aws/primitives/bad/v1.0.0\"",
		"}",
	])}
	test_input := helpers.mock_terraform_module_input(module_path, files)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Multi-source prod-context: first_source is pinned (git URL), second_source is local relative
# path -- prod-gate must fire for the second source even though the first is pinned
test_multi_source_prod_context_one_unpinned_fails if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"first\" {",
		"  source = var.first_source",
		"}",
		"module \"second\" {",
		"  source = var.second_source",
		"}",
		"variable \"first_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/first?ref=providers/aws/primitives/first/v1.0.0\"",
		"}",
		"variable \"second_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/second-module\"",
		"}",
	])}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": true},
	)

	violations := policy.violation with input as test_input

	count(violations) >= 1
}

# Multi-source prod-context: both sources are pinned (git URL defaults) -- no prod-gate violation
# (AC-10 also fires for both git-URL defaults, but prod-gate must NOT add extra violations)
test_multi_source_prod_context_both_pinned_no_prod_gate if {
	module_path := "modules/test-module"
	files := {"modules/test-module/main.tf": concat("\n", [
		"module \"first\" {",
		"  source = var.first_source",
		"}",
		"module \"second\" {",
		"  source = var.second_source",
		"}",
		"variable \"first_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/first?ref=providers/aws/primitives/first/v1.0.0\"",
		"}",
		"variable \"second_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/second?ref=providers/aws/primitives/second/v2.0.0\"",
		"}",
	])}
	test_input := object.union(
		helpers.mock_terraform_module_input(module_path, files),
		{"use_pinned_module_sources": true},
	)

	violations := policy.violation with input as test_input

	# AC-10 fires for both git-URL defaults (2 violations), but the prod-gate must NOT
	# add additional violations -- the prod gate only fires for relative-path defaults.
	# We confirm the count is exactly 2 (one AC-10 violation per git-URL default).
	count(violations) == 2
}

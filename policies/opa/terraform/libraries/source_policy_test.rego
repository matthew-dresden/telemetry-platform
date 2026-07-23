# Unit tests for the terraform module source policy library.
#
# Placed under policies/ so the `opa test policies` coverage gate
# (make rego-unit-test-coverage) discovers them: that command loads ONLY the
# policies/ tree, so coverage of source_policy.rego is achievable only by tests
# that also live here.
#
# Every assertion exercises real allow/deny behaviour of the policy through its
# public `violation` set; there are no trivially-true assertions.
package terraform.libraries.source.test

import data.terraform.libraries.source as policy
import data.terraform.libraries.source.test_helpers as fixtures

# Convenience: the set of violation policy ids produced for an input.
violation_messages(test_input) := {msg |
	some v in policy.violation with input as test_input
	msg := v.message
}

# ---------------------------------------------------------------------------
# Allowlisted host + pinned ?ref= semver -> ACCEPTED (no violation)
# ---------------------------------------------------------------------------

# telemetry-platform monorepo source with a pinned /v<semver> ref passes.
test_tools_telemetry_pinned_ref_passes if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"kms\" {",
		"  source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key?ref=providers/aws/primitives/kms-key/v1.0.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# terraform-modules monorepo source with a pinned /v<semver> ref passes.
test_terraform_modules_pinned_ref_passes if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"s3\" {",
		"  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/s3?ref=providers/aws/primitives/s3/v2.3.4\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# ---------------------------------------------------------------------------
# Host allowlist enforcement -> DENIED for any host outside the two monorepos
# ---------------------------------------------------------------------------

# A genuine third-party host (terraform-aws-modules org) is rejected.
test_third_party_host_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"ext\" {",
		"  source = \"git::https://github.com/terraform-aws-modules/terraform-aws-vpc.git//modules/vpc?ref=v5.0.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Module source must be from this monorepo" in violation_messages(test_input)
}

# Same example-org org but a non-allowlisted repo is rejected (allowlist is
# the two specific repos, not the whole org).
test_other_repo_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"bad\" {",
		"  source = \"git::https://github.com/example-org/other-repo.git//providers/aws/primitives/kms?ref=providers/aws/primitives/kms/v1.0.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Module source must be from this monorepo" in violation_messages(test_input)
}

# ---------------------------------------------------------------------------
# ?ref= semver pinning -> monorepo source missing/invalid ref is DENIED
# ---------------------------------------------------------------------------

# Allowlisted host but NO ?ref= at all -> missing-ref violation.
test_monorepo_missing_ref_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"kms\" {",
		"  source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Monorepo module source missing ref parameter" in violation_messages(test_input)
}

# Allowlisted host with a ?ref= that is a branch (no /v<semver>) -> still denied.
test_monorepo_non_semver_ref_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"kms\" {",
		"  source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key?ref=main\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Monorepo module source missing ref parameter" in violation_messages(test_input)
}

# terraform-modules host missing ref is likewise denied.
test_terraform_modules_missing_ref_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"s3\" {",
		"  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/s3\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Monorepo module source missing ref parameter" in violation_messages(test_input)
}

# ---------------------------------------------------------------------------
# Local-source rejection -> relative and absolute literal paths are DENIED
# ---------------------------------------------------------------------------

# A "../" relative local source is rejected as a local module source.
test_relative_local_source_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"local\" {",
		"  source = \"../other-module\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Local module source detected" in violation_messages(test_input)
}

# A "./" same-dir relative local source is also rejected.
test_dot_slash_local_source_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"local\" {",
		"  source = \"./submodule\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Local module source detected" in violation_messages(test_input)
}

# A "/absolute" literal local source is rejected.
test_absolute_local_source_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"abs\" {",
		"  source = \"/abs/path/to/module\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Local module source detected" in violation_messages(test_input)
}

# ---------------------------------------------------------------------------
# examples/ and tests/ exclusion -> local sources there are ALLOWED
# ---------------------------------------------------------------------------

# A local source under examples/ is excluded from policy and produces no violation.
test_examples_local_source_allowed if {
	files := {
		"modules/m/examples/complete/main.tf": "module \"x\" {\n  source = \"../../\"\n}",
		"modules/m/main.tf": "resource \"aws_s3_bucket\" \"b\" {}",
	}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# A local source under tests/ is excluded from policy and produces no violation.
test_tests_local_source_allowed if {
	files := {
		"modules/m/tests/unit/main.tf": "module \"x\" {\n  source = \"../../\"\n}",
		"modules/m/main.tf": "resource \"aws_s3_bucket\" \"b\" {}",
	}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# Files outside the module path are not scanned (tf_files_for_module prefix gate).
test_files_outside_module_path_ignored if {
	files := {"other/sibling/main.tf": "module \"x\" {\n  source = \"../local\"\n}"}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# Non-.tf files in the module path are not scanned (endswith .tf gate).
test_non_tf_files_ignored if {
	files := {"modules/m/README.md": "source = \"../local\""}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# ---------------------------------------------------------------------------
# Var-driven in-repo sources -> ALLOWED when the var contract is satisfied
# ---------------------------------------------------------------------------

# A single var-driven source with const=true and a relative default passes.
test_var_driven_inrepo_source_allowed if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# Multiple var-driven in-repo sources in one file all pass.
test_multiple_var_driven_inrepo_sources_allowed if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"a\" {",
		"  source = var.analytics_source",
		"}",
		"module \"b\" {",
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
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# ---------------------------------------------------------------------------
# Var contract -> const=true required, default must be in-repo relative path
# ---------------------------------------------------------------------------

# Var-driven source missing const=true -> const violation.
test_var_missing_const_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Source variable must declare const = true" in violation_messages(test_input)
}

# Var-driven source whose default is a git URL -> in-repo relative default violation.
test_var_git_url_default_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/glue?ref=providers/aws/primitives/glue/v1.0.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Source variable default must be an in-repo relative path" in violation_messages(test_input)
}

# Var-driven source whose default is an absolute path -> in-repo relative default violation.
test_var_absolute_default_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"/abs/path/to/module\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	"Source variable default must be an in-repo relative path" in violation_messages(test_input)
}

# ---------------------------------------------------------------------------
# External (terraform-modules) literal pinned source -> ALLOWED even when the
# repo also uses var-driven in-repo sources elsewhere.
# ---------------------------------------------------------------------------

# A literal terraform-modules pinned source coexisting with no other modules passes.
test_external_pinned_literal_allowed if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"vpc\" {",
		"  source = \"git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/collections/vpc?ref=providers/aws/collections/vpc/v2.1.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# ---------------------------------------------------------------------------
# Prod-context gate -> in-repo relative default DENIED only when pinned flag set
# ---------------------------------------------------------------------------

# use_pinned_module_sources=true + relative default -> prod-context violation.
test_prod_context_relative_default_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := fixtures.mock_module_input_pinned("modules/m", files, true)

	"In-repo source variable resolves to a local path in prod context" in violation_messages(test_input)
}

# use_pinned_module_sources=false + relative default -> allowed (gate dormant).
test_sandbox_context_relative_default_allowed if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := fixtures.mock_module_input_pinned("modules/m", files, false)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# No pinned flag at all -> treated as non-prod, relative default allowed.
test_no_pinned_flag_relative_default_allowed if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"glue\" {",
		"  source = var.glue_source",
		"}",
		"variable \"glue_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/glue-catalog-database\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	not "In-repo source variable resolves to a local path in prod context" in violation_messages(test_input)
}

# Prod context but the file lives under examples/ -> still exempt (no violation).
test_prod_context_examples_exempt if {
	files := {
		"modules/m/examples/complete/main.tf": concat("\n", [
			"module \"glue\" {",
			"  source = var.glue_source",
			"}",
			"variable \"glue_source\" {",
			"  type    = string",
			"  const   = true",
			"  default = \"../../primitives/glue-catalog-database\"",
			"}",
		]),
		"modules/m/main.tf": "resource \"aws_s3_bucket\" \"b\" {}",
	}
	test_input := fixtures.mock_module_input_pinned("modules/m", files, true)

	violations := policy.violation with input as test_input
	count(violations) == 0
}

# ---------------------------------------------------------------------------
# Multi-source files -> per-reference evaluation, not just the first match
# ---------------------------------------------------------------------------

# One valid var source + one missing const -> exactly one const violation.
test_multi_source_one_missing_const_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"good\" {",
		"  source = var.good_source",
		"}",
		"module \"bad\" {",
		"  source = var.bad_source",
		"}",
		"variable \"good_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/good\"",
		"}",
		"variable \"bad_source\" {",
		"  type    = string",
		"  default = \"../../primitives/bad\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	msgs := violation_messages(test_input)
	"Source variable must declare const = true" in msgs
}

# Multi-source prod context: one pinned git-URL default + one local relative
# default -> the prod gate fires for the second source only.
test_multi_source_prod_context_one_unpinned_denied if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"first\" {",
		"  source = var.first_source",
		"}",
		"module \"second\" {",
		"  source = var.second_source",
		"}",
		"variable \"first_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/first?ref=providers/aws/primitives/first/v1.0.0\"",
		"}",
		"variable \"second_source\" {",
		"  type    = string",
		"  const   = true",
		"  default = \"../../primitives/second\"",
		"}",
	])}
	test_input := fixtures.mock_module_input_pinned("modules/m", files, true)

	"In-repo source variable resolves to a local path in prod context" in violation_messages(test_input)
}

# ---------------------------------------------------------------------------
# Helper-predicate level assertions (exercise helpers directly to lock semantics)
# ---------------------------------------------------------------------------

test_is_var_driven_source_true if {
	policy.is_var_driven_source("  source = var.vpc_source\n")
}

test_is_var_driven_source_false_for_literal if {
	not policy.is_var_driven_source("  source = \"../local\"\n")
}

test_is_monorepo_source_true_for_tools_telemetry if {
	policy.is_monorepo_source("source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms?ref=x\"")
}

test_is_monorepo_source_false_for_third_party if {
	not policy.is_monorepo_source("source = \"git::https://github.com/terraform-aws-modules/terraform-aws-vpc.git//modules/vpc?ref=v5.0.0\"")
}

test_has_ref_parameter_true_for_semver if {
	policy.has_ref_parameter("source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms?ref=providers/aws/primitives/kms/v1.2.3\"")
}

test_has_ref_parameter_false_without_semver if {
	not policy.has_ref_parameter("source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms?ref=main\"")
}

test_var_names_from_source_extracts_all if {
	content := "source = var.first_source\nsource = var.second_source\n"
	policy.var_names_from_source(content) == {"first_source", "second_source"}
}

# Regression: an argument whose name ends in `_source` (e.g. a child-module config
# input `athena_data_source = var.athena_data_source`) is NOT a module source
# expression and must NOT be extracted as a source variable. The real `source =`
# attribute on the surrounding module block is still extracted.
test_var_names_from_source_ignores_data_source_argument if {
	content := "module \"q\" {\n  source = var.quicksight_source\n  athena_data_source = var.athena_data_source\n}\n"
	policy.var_names_from_source(content) == {"quicksight_source"}
}

test_is_var_driven_source_ignores_data_source_argument if {
	not policy.is_var_driven_source("  athena_data_source = var.athena_data_source\n")
}

test_is_var_driven_source_true_for_real_source if {
	policy.is_var_driven_source("  source = var.quicksight_source\n")
}

test_var_has_const_true_detects_const if {
	content := "variable \"x_source\" {\n  const = true\n}"
	policy.var_has_const_true(content, "x_source")
}

test_var_default_is_relative_path_true if {
	content := "variable \"x_source\" {\n  default = \"../../primitives/x\"\n}"
	policy.var_default_is_relative_path(content, "x_source")
}

test_var_default_is_git_url_true if {
	content := "variable \"x_source\" {\n  default = \"git::https://example\"\n}"
	policy.var_default_is_git_url(content, "x_source")
}

test_var_default_is_absolute_path_true if {
	content := "variable \"x_source\" {\n  default = \"/abs/path\"\n}"
	policy.var_default_is_absolute_path(content, "x_source")
}

test_contains_local_module_source_relative if {
	policy.contains_local_module_source("source = \"../m\"")
}

test_contains_local_module_source_absolute if {
	policy.contains_local_module_source("source = \"/abs\"")
}

test_contains_local_module_source_skips_var_driven if {
	not policy.contains_local_module_source("source = var.x_source")
}

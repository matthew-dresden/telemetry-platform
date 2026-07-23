package terraform.libraries.source

import future.keywords.contains
import future.keywords.if
import future.keywords.in

# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------

# True when the source value is a var-reference: source = var.<name>_source
# The leading (^|[^0-9A-Za-z_]) boundary ensures the `source` keyword is a standalone
# attribute name and not the tail of a longer identifier (e.g. an argument named
# `athena_data_source = var.athena_data_source`, which must NOT be treated as a module
# source expression). Terraform module sources are always the literal attribute `source`.
is_var_driven_source(content) if {
	regex.match(`(^|[^0-9A-Za-z_])source\s*=\s*var\.[a-zA-Z_][a-zA-Z0-9_]*_source\b`, content)
}

# True when the source literal is a monorepo git URL (example-org/telemetry-platform or matthew-dresden/terraform-modules).
# This is the single authoritative definition of the two-host allowlist.
is_monorepo_source(content) if {
	regex.match(`source\s*=\s*"git::https://github\.com/(matthew-dresden/terraform-modules|example-org/telemetry-platform)\.git//`, content)
}

# True when a monorepo source has a pinned /v<semver> ref.
has_ref_parameter(content) if {
	regex.match(`source\s*=\s*"git::https://github\.com/(matthew-dresden/terraform-modules|example-org/telemetry-platform)\.git//[^?]+\?ref=.+/v[0-9]+\.[0-9]+\.[0-9]+"`, content)
}

# True when the content contains a literal local source (relative or absolute path),
# excluding var-driven sources.
contains_local_module_source(content) if {
	not is_var_driven_source(content)
	regex.match(`source\s*=\s*"\.\.?/`, content)
}

contains_local_module_source(content) if {
	not is_var_driven_source(content)
	regex.match(`source\s*=\s*"/`, content)
}

# True when a *_source variable block has const = true declared.
var_has_const_true(content, var_name) if {
	# Match a variable "<var_name>" block containing const = true
	pattern := sprintf(`variable\s+"%s"\s*\{[^}]*const\s*=\s*true[^}]*\}`, [var_name])
	regex.match(pattern, content)
}

# True when a *_source variable's default is an in-repo relative path (../ style).
var_default_is_relative_path(content, var_name) if {
	# Match a variable "<var_name>" block containing default = "../..." or "../../..."
	pattern := sprintf(`variable\s+"%s"\s*\{[^}]*default\s*=\s*"\.\.`, [var_name])
	regex.match(pattern, content)
}

# True when a *_source variable's default is a git URL (disallowed).
var_default_is_git_url(content, var_name) if {
	pattern := sprintf(`variable\s+"%s"\s*\{[^}]*default\s*=\s*"git::`, [var_name])
	regex.match(pattern, content)
}

# True when a *_source variable's default is an absolute path (disallowed).
var_default_is_absolute_path(content, var_name) if {
	pattern := sprintf(`variable\s+"%s"\s*\{[^}]*default\s*=\s*"/`, [var_name])
	regex.match(pattern, content)
}

# Extract ALL variable names from var-driven source references in a file.
# Returns a SET of names (the full var name including the _source suffix).
# Uses n=-1 to find every match, not just the first.
# The leading (^|[^0-9A-Za-z_]) boundary ensures the `source` keyword is a standalone
# attribute name and not the tail of a longer identifier (e.g. an argument named
# `athena_data_source = var.athena_data_source`, which is a config input passed to a
# child module -- NOT a module source expression -- and must not be required to be const).
var_names_from_source(content) := {var_name |
	matches := regex.find_all_string_submatch_n(
		`(?:^|[^0-9A-Za-z_])source\s*=\s*var\.([a-zA-Z_][a-zA-Z0-9_]*_source)\b`,
		content,
		-1,
	)
	some match in matches
	var_name := match[1]
}

# ---------------------------------------------------------------------------
# tf_files comprehension helper -- shared across all violation rules.
# Excludes examples/ and tests/ paths.
# ---------------------------------------------------------------------------

tf_files_for_module(module_path) := {file |
	some path in object.keys(input.files)
	startswith(path, sprintf("%s/", [module_path]))
	endswith(path, ".tf")
	not contains(path, "/examples/")
	not contains(path, "/tests/")
	file := path
}

# ---------------------------------------------------------------------------
# Violation: literal local module source (excludes var-driven sources)
# ---------------------------------------------------------------------------

violation contains result if {
	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]
	contains_local_module_source(content)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "Local module source detected",
		"details": sprintf("File '%s' contains a reference to a local module source", [file]),
		"resolution": "Use remote module sources instead of local paths",
	}
}

# ---------------------------------------------------------------------------
# Violation: non-monorepo literal source (excludes var-driven sources)
# ---------------------------------------------------------------------------

violation contains result if {
	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]

	# Only applies to literal (non-var-driven) sources
	not is_var_driven_source(content)

	# File has a module block with a source attribute
	regex.match(`module\s+"[^"]+"\s+\{`, content)
	contains(content, "source")
	not is_monorepo_source(content)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "Module source must be from this monorepo",
		"details": sprintf("File '%s' contains a module source that is not from the platform module sources", [file]),
		"resolution": "Use module sources from git::https://github.com/matthew-dresden/terraform-modules.git or git::https://github.com/example-org/telemetry-platform.git only",
	}
}

# ---------------------------------------------------------------------------
# Violation: monorepo literal source missing pinned ref (excludes var-driven)
# ---------------------------------------------------------------------------

violation contains result if {
	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]

	# Only applies to literal (non-var-driven) sources
	not is_var_driven_source(content)

	# File has a monorepo source but is missing the pinned ref
	regex.match(`module\s+"[^"]+"\s+\{`, content)
	is_monorepo_source(content)
	not has_ref_parameter(content)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "Monorepo module source missing ref parameter",
		"details": sprintf("File '%s' contains a monorepo module source without a ?ref= parameter", [file]),
		"resolution": "Add ?ref=<version> to the module source URL",
	}
}

# ---------------------------------------------------------------------------
# Violation: var-driven source missing const = true on the declaring variable
# (spec section 4.5.2, AC-10)
# ---------------------------------------------------------------------------

violation contains result if {
	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]

	# Only applies to var-driven sources
	is_var_driven_source(content)

	# Iterate over ALL var names found in this file
	some var_name in var_names_from_source(content)

	# The declaring variable exists in the same file but lacks const = true
	not var_has_const_true(content, var_name)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "Source variable must declare const = true",
		"details": sprintf("File '%s': variable '%s' used in module source must declare 'const = true' (required for Terraform source expressions)", [file, var_name]),
		"resolution": "Add 'const = true' to the variable declaration for the source variable",
	}
}

# ---------------------------------------------------------------------------
# Violation: var-driven source whose variable default is not an in-repo relative path
# (spec section 4.5.2, AC-10)
# ---------------------------------------------------------------------------

violation contains result if {
	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]

	# Only applies to var-driven sources
	is_var_driven_source(content)

	# Iterate over ALL var names found in this file
	some var_name in var_names_from_source(content)

	# The declaring variable has a git URL as its default
	var_default_is_git_url(content, var_name)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "Source variable default must be an in-repo relative path",
		"details": sprintf("File '%s': variable '%s' default is a git URL -- source variable defaults must be in-repo relative paths (e.g. '../../primitives/module-name')", [file, var_name]),
		"resolution": "Set the variable default to a relative path such as '../../primitives/<module>'; pinned git URLs are injected at deploy time via terragrunt inputs",
	}
}

violation contains result if {
	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]

	# Only applies to var-driven sources
	is_var_driven_source(content)

	# Iterate over ALL var names found in this file
	some var_name in var_names_from_source(content)

	# The declaring variable has an absolute path as its default
	var_default_is_absolute_path(content, var_name)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "Source variable default must be an in-repo relative path",
		"details": sprintf("File '%s': variable '%s' default is an absolute path -- source variable defaults must be in-repo relative paths (e.g. '../../primitives/module-name')", [file, var_name]),
		"resolution": "Set the variable default to a relative path such as '../../primitives/<module>'; absolute paths are not portable across environments",
	}
}

# ---------------------------------------------------------------------------
# Violation: prod-context gate -- in-repo var-driven source resolves to local default
# when use_pinned_module_sources is true (spec section 4.5.4, AC-12, AC-21)
# ---------------------------------------------------------------------------

violation contains result if {
	# Only fires when the input signals prod context
	input.use_pinned_module_sources == true

	module_path := input.module_path
	tf_files := tf_files_for_module(module_path)

	some file in tf_files
	content := input.files[file]

	# Only applies to var-driven sources
	is_var_driven_source(content)

	# Iterate over ALL var names found in this file
	some var_name in var_names_from_source(content)

	# The variable default resolves to a local/relative path (unpinned)
	var_default_is_relative_path(content, var_name)

	result := {
		"policy": "terraform_module_source_policy",
		"severity": "error",
		"message": "In-repo source variable resolves to a local path in prod context",
		"details": sprintf("File '%s': variable '%s' default is a relative path but use_pinned_module_sources is true -- prod context requires a pinned git URL", [file, var_name]),
		"resolution": "Ensure the terragrunt leaf passes a pinned git URL via inputs when use_pinned_module_sources = true",
	}
}

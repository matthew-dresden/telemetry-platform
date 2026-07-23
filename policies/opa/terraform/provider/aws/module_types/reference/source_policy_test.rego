# Unit tests for the reference module-type source policy re-export.
#
# The reference policy is a thin re-export: `violation := source.violation`.
# These tests prove the re-export actually surfaces the library's deny/allow
# decisions (it is not an empty set or a stale copy) by evaluating the
# re-exported `violation` directly and comparing it to the library result.
package terraform.provider.aws.module_types.reference.source.test

import data.terraform.libraries.source as library
import data.terraform.libraries.source.test_helpers as fixtures
import data.terraform.provider.aws.module_types.reference.source as reexport

# A denied input surfaces through the re-export with the same violations the
# library produces (missing-ref violation in this case).
test_reexport_surfaces_denied_violation if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"kms\" {",
		"  source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	reexport_violations := reexport.violation with input as test_input
	library_violations := library.violation with input as test_input

	count(reexport_violations) >= 1
	reexport_violations == library_violations
}

# An allowed input surfaces zero violations through the re-export.
test_reexport_allows_pinned_source if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"net\" {",
		"  source = \"git::https://github.com/example-org/telemetry-platform.git//providers/aws/references/vpc-network?ref=providers/aws/references/vpc-network/v2.1.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := reexport.violation with input as test_input
	count(violations) == 0
}

# A var-driven source missing const=true is denied through the re-export.
test_reexport_denies_missing_const if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"net\" {",
		"  source = var.net_source",
		"}",
		"variable \"net_source\" {",
		"  type    = string",
		"  default = \"../../references/vpc-network\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	reexport_violations := reexport.violation with input as test_input
	some v in reexport_violations
	v.message == "Source variable must declare const = true"
}

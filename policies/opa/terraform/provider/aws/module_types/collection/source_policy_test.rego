# Unit tests for the collection module-type source policy re-export.
#
# The collection policy is a thin re-export: `violation := source.violation`.
# These tests prove the re-export actually surfaces the library's deny/allow
# decisions (it is not an empty set or a stale copy) by evaluating the
# re-exported `violation` directly and comparing it to the library result.
package terraform.provider.aws.module_types.collection.source.test

import data.terraform.libraries.source as library
import data.terraform.libraries.source.test_helpers as fixtures
import data.terraform.provider.aws.module_types.collection.source as reexport

# A denied input surfaces through the re-export with the same violations the
# library produces (host-allowlist violation in this case).
test_reexport_surfaces_denied_violation if {
	files := {"modules/m/main.tf": concat("\n", [
		"module \"ext\" {",
		"  source = \"git::https://github.com/terraform-aws-modules/terraform-aws-vpc.git//modules/vpc?ref=v5.0.0\"",
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
		"module \"vpc\" {",
		"  source = \"git::https://github.com/caylent-solutions/terraform-modules.git//providers/aws/collections/vpc?ref=providers/aws/collections/vpc/v2.1.0\"",
		"}",
	])}
	test_input := fixtures.mock_module_input("modules/m", files)

	violations := reexport.violation with input as test_input
	count(violations) == 0
}

# A local-source input is denied through the re-export.
test_reexport_denies_local_source if {
	files := {"modules/m/main.tf": "module \"local\" {\n  source = \"../other\"\n}"}
	test_input := fixtures.mock_module_input("modules/m", files)

	reexport_violations := reexport.violation with input as test_input
	some v in reexport_violations
	v.message == "Local module source detected"
}

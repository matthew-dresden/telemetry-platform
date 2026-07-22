# Shared test fixtures for the terraform module source policy unit tests.
#
# These helpers live under policies/ (not tests/) because the rego coverage gate
# runs `opa test policies` -- it loads ONLY the policies/ tree, so any helper a
# *_test.rego file imports must also reside under policies/. The package name is
# kept distinct from tests.opa.unit.helpers to avoid any collision if both trees
# are ever loaded into the same evaluation.
package terraform.libraries.source.test_helpers

# Build a mock terraform-module evaluation input from a module path and a
# {path: content} file map. Mirrors the shape the module validator passes to the
# source policy at runtime (module_path + files object keyed by relative path).
mock_module_input(module_path, files) := {
	"module_path": module_path,
	"repo_path": module_path,
	"files": files,
}

# Build a mock input that additionally carries the prod-context flag the policy
# reads via input.use_pinned_module_sources (spec 4.5.4, AC-12/AC-21).
mock_module_input_pinned(module_path, files, pinned) := object.union(
	mock_module_input(module_path, files),
	{"use_pinned_module_sources": pinned},
)

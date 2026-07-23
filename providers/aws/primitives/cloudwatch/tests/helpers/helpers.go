// Package helpers provides shared test utilities for the cloudwatch primitive module tests.
package helpers

import (
	"testing"

	"github.com/gruntwork-io/terratest/modules/terraform"
)

// ReadDashboardArnNullable reads the dashboard_arn output without erroring when the value is null.
// Terraform 1.x does not store null outputs in state, so terraform.Output panics on null outputs.
// This function reads all outputs as a map via terraform.OutputAll and returns nil when dashboard_arn
// is absent or null (create_dashboard=false), or the raw interface{} value otherwise. Callers must
// still assert the returned value to ensure the test can fail when the module misbehaves.
func ReadDashboardArnNullable(t *testing.T, opts *terraform.Options) interface{} {
	t.Helper()
	return terraform.OutputAll(t, opts)["dashboard_arn"]
}

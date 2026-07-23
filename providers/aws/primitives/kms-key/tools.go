//go:build tools

package tools

import (
	_ "github.com/gruntwork-io/terratest/modules/terraform"
	_ "github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
)

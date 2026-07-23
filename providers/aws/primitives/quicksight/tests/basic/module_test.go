//go:build terratest

package basic_test

import (
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/matthew-dresden/terraform-terratest-framework/pkg/assertions"
	"github.com/matthew-dresden/terraform-terratest-framework/pkg/testctx"
)

// mustTaggingVars reads PROJECT_TAG and TERRATEST_RUN_ID from the environment.
// It calls t.Fatal with an actionable error message when either variable is unset.
// ExtraVars override committed tfvars values (spec section 4.3, D-12).
func mustTaggingVars(t *testing.T) map[string]interface{} {
	t.Helper()
	runID := os.Getenv("TERRATEST_RUN_ID")
	if runID == "" {
		t.Fatal("ERROR: TERRATEST_RUN_ID is not set; run via make tf-test")
	}
	projectTag := os.Getenv("PROJECT_TAG")
	if projectTag == "" {
		t.Fatal("ERROR: PROJECT_TAG is not set; run via make tf-test")
	}
	return map[string]interface{}{
		"project_tag":      projectTag,
		"terratest_run_id": runID,
	}
}

// TestBasicQuickSightNoDataSourceCreated asserts no aws_quicksight_data_source is created in the basic example.
func TestBasicQuickSightNoDataSourceCreated(t *testing.T) {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	ctx := testctx.RunSingleExample(t, "../../examples", "basic", testctx.TestConfig{
		Name:      fmt.Sprintf("qs-basic-no-ds-%s", suffix),
		ExtraVars: mustTaggingVars(t),
	})

	assertions.AssertResourceCount(t, ctx, "aws_quicksight_data_source", 0)
}

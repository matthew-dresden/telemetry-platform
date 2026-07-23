// Package helpers provides shared test utilities for the s3-bucket primitive module tests.
package helpers

import (
	"fmt"
	"strings"
	"time"
)

// GenerateUniqueBucketName returns a globally unique S3 bucket name with the given prefix.
// S3 bucket names must be globally unique, 3-63 chars, lowercase letters, numbers, and hyphens only.
// The suffix is derived from the current Unix timestamp to avoid collisions between parallel runs.
func GenerateUniqueBucketName(prefix string) string {
	suffix := fmt.Sprintf("%d", time.Now().Unix())
	// Truncate prefix to leave room for suffix and hyphen (max 63 chars total)
	// suffix is 10 digits, plus hyphen = 11 chars; prefix max = 52 chars
	maxPrefixLen := 52
	if len(prefix) > maxPrefixLen {
		prefix = prefix[:maxPrefixLen]
	}
	name := fmt.Sprintf("%s-%s", strings.ToLower(prefix), suffix)
	return name
}

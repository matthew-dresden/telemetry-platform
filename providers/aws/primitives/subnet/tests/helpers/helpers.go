package helpers

import (
	"fmt"
	"sync"
)

var (
	cidrMu      sync.Mutex
	cidrCounter int
)

// GenerateUniqueCIDR returns a unique /24 CIDR block from the 10.x.x.0/24 space.
// It is safe for concurrent use across parallel Terratest runs within the same process.
func GenerateUniqueCIDR() string {
	cidrMu.Lock()
	defer cidrMu.Unlock()
	cidrCounter++
	// Cycle through 10.0.0.0/24 - 10.255.255.0/24 (16,777,216 /24 blocks)
	third := (cidrCounter / 256) % 256
	fourth := cidrCounter % 256
	return fmt.Sprintf("10.%d.%d.0/24", third, fourth)
}

// GenerateVPCCIDR returns a unique /16 CIDR block from the 10.x.0.0/16 space.
// It is safe for concurrent use across parallel Terratest runs within the same process.
func GenerateVPCCIDR() string {
	cidrMu.Lock()
	defer cidrMu.Unlock()
	cidrCounter++
	second := cidrCounter % 256
	return fmt.Sprintf("10.%d.0.0/16", second)
}

# subnet -- tests/helpers

Shared helper utilities for the subnet module's Terratest suites.

## GenerateUniqueCIDR

Returns a unique `/24` CIDR block from the `10.x.x.0/24` space. Thread-safe via a mutex, making it safe for parallel Terratest runs within the same process.

```go
import "github.com/example-org/telemetry-platform/providers/aws/primitives/subnet/tests/helpers"

cidr := helpers.GenerateUniqueCIDR() // e.g. "10.0.1.0/24"
```

## GenerateVPCCIDR

Returns a unique `/16` CIDR block from the `10.x.0.0/16` space. Thread-safe via a mutex.

```go
vpcCIDR := helpers.GenerateVPCCIDR() // e.g. "10.1.0.0/16"
```

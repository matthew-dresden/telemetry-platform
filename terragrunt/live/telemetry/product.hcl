# live/telemetry/product.hcl
#
# Product layer: basename resolves to "telemetry" (spec 02 Section 1.3).
# This is the canonical product layer idiom from the reference.

locals {
  product = basename(get_terragrunt_dir())
}

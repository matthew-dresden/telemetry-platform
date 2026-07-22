# live/telemetry/us-east-1/region.hcl
#
# Region layer: basename resolves to "us-east-1" (spec 02 Section 1.3).
# This file is SHARED by all account subtrees under us-east-1/ (D1).
# A leaf under any account sub-tree finds this single file via find_in_parent_folders.

locals {
  aws_region = basename(get_terragrunt_dir())
}

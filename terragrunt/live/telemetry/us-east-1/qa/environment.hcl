# live/telemetry/us-east-1/qa/environment.hcl
#
# QA environment layer.
# Basename resolves to "qa" per the canonical layer idiom (spec 02 Section 1.3).
# Every service unit in this account under the qa environment lives at
# .../qa/000/<service>/000/terragrunt.hcl.
#
# The "qa" environment label is consumed by the root terragrunt.hcl to derive
# the remote-state key prefix. All service-tree units in the qa account share
# this environment label and resolve to distinct state keys via their service
# and service_instance segments.
#
# Layer hierarchy (spec 02 Section 1.3):
#   account.hcl              -- account-level locals (aws_account_id, domain apexes, etc.)
#   environment.hcl          -- THIS FILE: environment label "qa"
#   environment_instance.hcl -- instance label "000"
#   service.hcl              -- service label (e.g. "dns-prod-zone")
#   service_instance.hcl     -- instance label "000"
#   terragrunt.hcl           -- leaf unit (source + inputs)

locals {
  # environment resolves to the directory basename, i.e. "qa".
  # This value is referenced by the root terragrunt.hcl remote_state key
  # derivation and must not be overridden at any child layer.
  environment = basename(get_terragrunt_dir())
}

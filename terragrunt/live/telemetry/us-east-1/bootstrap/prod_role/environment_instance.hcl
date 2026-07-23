# bootstrap/<role>/environment_instance.hcl
# The bootstrap env-instance layer = the ROLE token, which is the dir basename VERBATIM:
# one of sandbox_role / prod_role / qa_role / dns_owner_role. This is the "*_role" 4th-field
# sub-class (the three sub-classes are: numeric set NNN / bare tier word shared|dns_owner|pretty
# / bootstrap role *_role). The root.hcl tier local classifies "*_role" as the "bootstrap" tier
# (per-account bootstrap). Basename-derived so the folder mirrors the namespace
# telemetry-useast1-bootstrap-<role>-<unit>-<svcinst>.
locals {
  environment_instance = basename(get_terragrunt_dir())
}

# terraform.tfvars -- collector-ingestion deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# DEPLOYMENT-UNIQUE FIXED VALUES (spec section 0.5, AC-FUNC-002):
#   adot_receiver_max_request_body_size -- ADOT OTLP HTTP receiver maximum request
#   body size in bytes. This value was previously inlined in the leaf terragrunt.hcl
#   inputs block. It maps to the module variable adot_receiver_max_request_body_size
#   declared in references/collector-ingestion (variables.tf:107), which flows into
#   receivers.otlp.protocols.http.max_request_body_size (locals.tf:30). The value
#   4194304 (4 MiB) is the deployment-specific sizing for this sandbox instance (D44).
#
# Note: _envcommon/collector-ingestion.hcl also carries max_request_body_size = 4194304
# (line 50). That _envcommon input and the module variable adot_receiver_max_request_body_size
# are the SAME ADOT OTLP HTTP receiver body-size value being wired through -- they are not
# separate knobs. The _envcommon name comes from the OTLP HTTP receiver configuration
# field (receivers.otlp.protocols.http.max_request_body_size); the module variable name
# adot_receiver_max_request_body_size is the Terraform-idiomatic form. Only
# adot_receiver_max_request_body_size is declared as a module variable
# (references/collector-ingestion/variables.tf:107). The inline literal in
# the leaf's inputs block is removed and sourced from this file instead (AC-FUNC-002).
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).

adot_receiver_max_request_body_size = 4194304

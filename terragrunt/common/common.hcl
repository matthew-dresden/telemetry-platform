# terragrunt/common/common.hcl
#
# Shared locals and JSON map loading for all scope-shared, non-derivable values
# (spec S4.1). This file is the single place where all four JSON registry maps
# are loaded; callers never re-read the JSON themselves (DRY).
#
# This file is read by the root config and every leaf via:
#   common_vars = read_terragrunt_config("${get_repo_root()}/terragrunt/common/common.hcl")
#
# It exposes:
#   1. Base shared locals: common_tags, default_region, terraform_modules_path.
#   2. The four raw JSON maps (accounts, domains, networks, contacts) -- loaded
#      ONCE here via get_repo_root()-anchored paths so common/ resolves from any
#      copied subtree depth (spec S3, D3). Callers perform keyed lookups against
#      these maps using the lookup(map, key, null)-then-tobool("ERROR: ...") idiom
#      documented in spec S4.1/S7: accounts keyed by derived account id; domains
#      and contacts keyed by derived env-class; networks keyed by full namespace.
#   3. The dns_owner_zone_id fail-fast guard -- the only scope-invariant lookup
#      this file can perform directly (keyed by is_dns_owner=true in accounts.json,
#      derivable without the calling leaf's context). This guard fires at parse time
#      from any caller of common.hcl (spec AC-5, S0.2, S7).
#
# Terragrunt 1.0.7 evaluation-context constraint (spec section 4.3):
#   When read via read_terragrunt_config(), Terragrunt evaluates this file's
#   locals in THIS file's own directory context (terragrunt/common/). The
#   calling leaf's context -- in particular the caller-derived keys used for
#   per-scope keyed lookups (aws_account_id, environment_name, namespace) --
#   is NOT available inside this file's locals evaluation.
#
#   Consequence: per-scope keyed lookups (accounts[aws_account_id],
#   domains[environment_name], contacts[environment_name], networks[namespace])
#   MUST be performed at the root terragrunt.hcl layer (spec section 4.3, task
#   E8-F2-S1-T1) rather than here. The canonical lookup-then-tobool idiom for
#   those callers is documented in comment blocks below (spec AC-5, S4.1).
#
#   The dns_owner_zone_id guard IS performed here because its key (the account
#   with is_dns_owner=true) is derivable from the accounts map itself without
#   any caller-scope context.
#
# Ledger decisions applied here:
#   D3   - common/ is OUTSIDE the copy boundary; paths anchored on get_repo_root()
#   S3.5 - no fallback logic for identity-scoped lookups
#   S4.1 - shared locals + JSON map loading centralised here (DRY)
#   S7   - fail-fast at parse, naming scope + file + remediation

locals {
  # ---------------------------------------------------------------------------
  # Base shared locals (spec S4.1, AC-3)
  # ---------------------------------------------------------------------------

  # Default AWS region -- callers may override per their hierarchy locals.
  default_region = "us-east-1"

  # Canonical path to the reference/primitive module root.
  # All leaf terraform.source values MUST reference modules under this path.
  terraform_modules_path = "${get_repo_root()}/providers/aws"

  # Base common_tags included in every deployment. Callers extend this map
  # with their own scope-derived keys (product_family, region, account_id, etc.)
  # via the root terragrunt.hcl common_tags derivation.
  common_tags = {
    terraform  = "true"
    terragrunt = "true"
  }

  # ---------------------------------------------------------------------------
  # JSON map loading (spec S4.1, AC-3)
  #
  # Every map is loaded from "${get_repo_root()}"/terragrunt/common/<map>.json so
  # common/ resolves from any copied subtree depth. NO relative paths are used.
  # The get_repo_root()-anchored path is the only supported read mechanism (spec D3).
  # Malformed JSON surfaces as a jsondecode parse error at Terragrunt parse time.
  # ---------------------------------------------------------------------------

  accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))
  domains  = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
  networks = jsondecode(file("${get_repo_root()}/terragrunt/common/networks.json"))
  contacts = jsondecode(file("${get_repo_root()}/terragrunt/common/contacts.json"))

  # Vendor-neutral platform identity (org/repo/product/region/module host). The ONE
  # file a consumer edits to point the deployment at their own GitHub org/repo. See
  # terragrunt/common/platform.json.
  platform = jsondecode(file("${get_repo_root()}/terragrunt/common/platform.json"))

  # Git base for pinned in-repo module sources. Leaf terraform.source values use this
  # when use_pinned_module_sources=true so a fork resolves its own org/repo with zero
  # leaf edits: "git::https://<host>/<org>/<repo>.git". Derived from platform.json.
  module_git_base = "git::https://${local.platform.module_source_host}/${local.platform.org}/${local.platform.repo}.git"

  # ---------------------------------------------------------------------------
  # DNS-owner zone-id guard (spec AC-5, S0.2, S7)
  #
  # The dns-owner account is uniquely identified by is_dns_owner = true in
  # accounts.json. Its dns_owner_zone_id must be populated with a real Route53
  # hosted zone id (not the "<REAL_Z_ID>" placeholder) before the dns-owner
  # account is deployed. This guard fires at Terragrunt parse time from any
  # caller of common.hcl.
  #
  # Fail-fast idiom: lookup(map, key, null) -> tobool("ERROR: ...")
  # ---------------------------------------------------------------------------

  # Identify the dns-owner account id from the accounts map.
  _dns_owner_account_id = [
    for acct_id, cfg in local.accounts :
    acct_id
    if lookup(cfg, "is_dns_owner", false) == true
  ]

  # Resolve the dns-owner entry (null-safe: if no dns-owner exists, no guard fires).
  _dns_owner_entry = length(local._dns_owner_account_id) > 0 ? lookup(
    local.accounts,
    local._dns_owner_account_id[0],
    null
  ) : null

  # Resolve the raw zone id from the dns-owner entry.
  _dns_owner_zone_id_raw = local._dns_owner_entry != null ? lookup(
    local._dns_owner_entry, "dns_owner_zone_id", null
  ) : null

  # Guard: the zone id must be non-null and must not be the placeholder string.
  # tobool("ERROR: ...") aborts Terragrunt parse with the given message if the
  # condition is false (lazy ternary evaluation -- the false branch runs only
  # when the condition is false).
  dns_owner_zone_id = (
    local._dns_owner_zone_id_raw != null &&
    local._dns_owner_zone_id_raw != "<REAL_Z_ID>"
    ) ? local._dns_owner_zone_id_raw : tobool(
    "ERROR: dns_owner_zone_id in common/accounts.json is missing or contains the placeholder '<REAL_Z_ID>'. Replace <REAL_Z_ID> with the real Route53 hosted zone id for the dns-owner account before deploying."
  )

  # ---------------------------------------------------------------------------
  # Accounts resolver (spec AC-4, S4.1)
  #
  # Callers resolve an account config as:
  #   account_cfg = lookup(local.common_vars.locals.accounts, local.aws_account_id, null) != null
  #     ? lookup(local.common_vars.locals.accounts, local.aws_account_id, null)
  #     : tobool("ERROR: account id '${local.aws_account_id}' not found in common/accounts.json -- add a row for this account id before deploying this scope.")
  #
  # The fail-fast pattern is: lookup(accounts, <derived account id>, null) then
  # tobool("ERROR: ...") if null, naming the account id + accounts.json + remediation,
  # so a wrong or unmapped account id aborts parse BEFORE any AWS call (spec AC-4, S7).
  # No fallback value is provided -- the tobool abort is the only branch (spec S3.5).
  # ---------------------------------------------------------------------------

  # ---------------------------------------------------------------------------
  # Domains resolver (spec AC-5, S4.1)
  #
  # Callers resolve a domain config as:
  #   domain_cfg = lookup(local.common_vars.locals.domains, local.environment_name, null) != null
  #     ? lookup(local.common_vars.locals.domains, local.environment_name, null)
  #     : tobool("ERROR: env-class '${local.environment_name}' not found in common/domains.json -- add a row for this env-class before deploying.")
  #
  # Fail-fast semantics: a missing env-class key aborts parse with an actionable
  # message naming the scope + domains.json + remediation. No fallback (spec S3.5).
  # ---------------------------------------------------------------------------

  # ---------------------------------------------------------------------------
  # Networks resolver (spec AC-5, S4.1, S3.6)
  #
  # Callers (VPC-creating units ONLY) resolve a CIDR as:
  #   network_cfg = lookup(local.common_vars.locals.networks, local.namespace, null) != null
  #     ? lookup(local.common_vars.locals.networks, local.namespace, null)
  #     : tobool("ERROR: namespace '${local.namespace}' not found in common/networks.json -- add a CIDR row for this VPC-creating unit's namespace before deploying.")
  #
  # Non-VPC units MUST NOT key this map (spec S3.6). The networks map is keyed by
  # the full derived namespace so CIDR allocation is per-deployment unique.
  # Fail-fast semantics: missing CIDR aborts parse; no fallback (spec S3.5).
  # ---------------------------------------------------------------------------

  # ---------------------------------------------------------------------------
  # Contacts resolver (spec AC-5, S4.1)
  #
  # Callers resolve contacts as:
  #   contacts_cfg = lookup(local.common_vars.locals.contacts, local.environment_name, null) != null
  #     ? lookup(local.common_vars.locals.contacts, local.environment_name, null)
  #     : tobool("ERROR: env-class '${local.environment_name}' not found in common/contacts.json -- add a row for this env-class before deploying.")
  #
  # Fail-fast semantics: a missing env-class key aborts parse with an actionable
  # message naming the scope + contacts.json + remediation. No fallback (spec S3.5).
  # ---------------------------------------------------------------------------
}

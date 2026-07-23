# live/telemetry/us-east-1/<env>/000/identity/000/terragrunt.hcl
#
# Service account identity unit.
# Maps IAM Identity Center groups (Viewer, Author, Admin) to the QuickSight,
# Athena, and portal access roles via the references/identity reference module
# (AC-3, E1-F7-S2-T2). Sources exactly ONE module per AC-3: no primitive is
# sourced directly from this leaf.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight (scripts.tf_state_preflight) before plan/apply to confirm the
# sandbox state backend (S3 bucket + DynamoDB lock table) was created by state-bootstrap.
# The root remote_state block will fail closed if the bucket does not exist.
#
# IDENTITY CONTRACT (D8/D37/AC-13):
# Viewer, Author, and Admin Identity Center group names and the QuickSight role names
# are sourced from _envcommon/identity.hcl (not hardcoded here). The trust policies
# and inline policies are declared as locals below so all values remain parameterised
# and environment-agnostic (D31). A missing or empty group name input causes the
# references/identity variable validation to fail fast before any role is created.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D8, D31, D37, D40, D45, D47, AC-3, AC-13.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/identity.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/identity?ref=providers/aws/references/identity/v1.0.1" : "${get_repo_root()}//providers/aws/references/identity"
}

# ---------------------------------------------------------------------------
# locals: account identity and input-driven policy documents (D31, D37, AC-13)
# ---------------------------------------------------------------------------

locals {
  # Account-level locals from the D2 guard -- consumed for permission_set_account_id
  # and as the trust-policy principal account (D45/D47, not a hardcoded prod literal).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # ---------------------------------------------------------------------------
  # Trust policy for the TelemetryAnalyst role (Author permission set, D2r).
  # Trusts the sandbox account root so IAM Identity Center SSO can assume the role
  # via the permission set assignment (spec/iac/04 section 1.D). The ExternalId
  # condition binds the trust to the author group name -- no wildcard principals.
  # All values are derived from account.hcl locals; no literals are hardcoded (D31).
  # ---------------------------------------------------------------------------
  analyst_trust_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowIdentityCenterAssumption"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.aws_account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "identity-center-telemetry-author"
          }
        }
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # Trust policy for the TelemetryAdmin role (Admin permission set, D3r).
  # Mirrors the analyst trust pattern with the admin group ExternalId condition.
  # ---------------------------------------------------------------------------
  admin_trust_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowIdentityCenterAssumption"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.aws_account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "identity-center-telemetry-admin"
          }
        }
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # Trust policy for the ECS task and execution roles (spec/iac/04 sections 3.1, 3.2).
  # _envcommon/identity.hcl sets non-empty ecs_task_role_name / ecs_task_execution_role_name,
  # so references/identity creates both roles (count = 1) and the iam-role primitive
  # requires a non-null assume_role_policy_json for each (variables.tf validation).
  # The ECS service principal (ecs-tasks.amazonaws.com) is the standard trust for ECS
  # task and execution roles; both roles share the same assume-role trust document
  # (identical to references/identity/examples/with-ecs-roles). No account literal is
  # embedded -- the principal is the AWS-global ECS tasks service (D31).
  # ---------------------------------------------------------------------------
  ecs_trust_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTasksAssumption"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # D2r analyst inline policy -- read-only Athena/Glue/S3-results (spec/iac/04 1.D).
  # All resource ARNs are scoped to the sandbox account id (D45 -- no prod ARNs).
  # ---------------------------------------------------------------------------
  analyst_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaQueryExecution"
        Effect = "Allow"
        Action = [
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StartQueryExecution",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup",
        ]
        Resource = "arn:aws:athena:*:${local.aws_account_id}:workgroup/telemetry-*"
      },
      {
        Sid    = "GlueReadAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartition",
          "glue:GetPartitions",
        ]
        Resource = [
          "arn:aws:glue:*:${local.aws_account_id}:catalog",
          "arn:aws:glue:*:${local.aws_account_id}:database/telemetry_*",
          "arn:aws:glue:*:${local.aws_account_id}:table/telemetry_*/*",
        ]
      },
      {
        Sid    = "AthenaResultsS3Access"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:PutObject",
        ]
        Resource = [
          "arn:aws:s3:::telemetry-athena-results-${local.aws_account_id}",
          "arn:aws:s3:::telemetry-athena-results-${local.aws_account_id}/*",
        ]
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # D3r admin inline policy -- manage QuickSight/datasets (spec/iac/04 1.D).
  # Scoped to the sandbox account id (D45). No prod ARNs hardcoded (D31).
  # ---------------------------------------------------------------------------
  admin_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "QuickSightDatasetManagement"
        Effect = "Allow"
        Action = [
          "quicksight:CreateDataSet",
          "quicksight:DeleteDataSet",
          "quicksight:DescribeDataSet",
          "quicksight:ListDataSets",
          "quicksight:UpdateDataSet",
          "quicksight:CreateDataSource",
          "quicksight:DeleteDataSource",
          "quicksight:DescribeDataSource",
          "quicksight:UpdateDataSource",
          "quicksight:ListDashboards",
          "quicksight:DescribeDashboard",
          "quicksight:CreateDashboard",
          "quicksight:UpdateDashboard",
          "quicksight:DeleteDashboard",
          "quicksight:DescribeUser",
          "quicksight:ListUsers",
          "quicksight:RegisterUser",
          "quicksight:DeleteUser",
        ]
        Resource = "arn:aws:quicksight:*:${local.aws_account_id}:*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# inputs: identity reference module (AC-3, D37, D45, D47)
#
# Group names and role names are merged from _envcommon/identity.hcl
# (via the "envcommon" include above). The policy JSON inputs are declared
# as locals above and passed here so all values remain parameterised (D31).
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true, making root locals
# accessible only via include.root.locals.<name> at leaf scope (spec 02).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Child module source overrides (AC-7, AC-8, spec Section 4.3, Section 5).
# When use_pinned_module_sources=true (prod): every in-repo child source is set to
# its pinned git URL so the composed module tree is fully pinned.
# When use_pinned_module_sources=false (sandbox/QA/root): the *_source keys are
# OMITTED entirely so the module's relative-path defaults apply (spec Section 4.3,
# Leaf behavior toggle=false: "Does NOT set any *_source inputs"). They MUST be
# omitted -- not set to null -- because a terraform child module `source` argument
# is a const-typed variable; passing an explicit null overrides the relative-path
# default and crashes `terraform init` ("panic: value is null" in module install).
# A conditional merge adds the keys only in the pinned (prod) context.
# ---------------------------------------------------------------------------
inputs = merge(
  {
    analyst_assume_role_policy_json = local.analyst_trust_policy_json
    admin_assume_role_policy_json   = local.admin_trust_policy_json

    # ECS task and execution role trust policies (spec/iac/04 sections 3.1, 3.2).
    # Required because _envcommon/identity.hcl sets non-empty ECS role names, so the
    # roles are created and the iam-role primitive validates assume_role_policy_json
    # is non-null. Both share the ecs-tasks.amazonaws.com service trust document.
    ecs_task_assume_role_policy_json           = local.ecs_trust_policy_json
    ecs_task_execution_assume_role_policy_json = local.ecs_trust_policy_json

    analyst_inline_policies = {
      AnalystReadOnlyPolicy = local.analyst_policy
    }
    admin_inline_policies = {
      AdminQuickSightPolicy = local.admin_policy
    }

    tags = include.root.locals.common_tags
  },
  local.account_vars.locals.use_pinned_module_sources ? {
    analyst_source            = "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1"
    admin_source              = "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1"
    ecs_task_source           = "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1"
    ecs_task_execution_source = "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1"
  } : {}
)

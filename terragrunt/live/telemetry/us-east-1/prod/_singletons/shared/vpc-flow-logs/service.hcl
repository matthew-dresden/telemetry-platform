# live/telemetry/us-east-1/<env>/_singletons/shared/vpc-flow-logs/service.hcl
#
# Service layer for the vpc-flow-logs unit.
# Basename resolves to "vpc-flow-logs" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit provisions the externally-owned VPC Flow Logs destination + delivery role that
# the collector-ingestion reference (v1.0.2) requires when enable_flow_logs = true (its
# secure-by-default; variables.tf:194-220). The composed vpc-network primitive creates the
# aws_flow_log with iam_role_arn = var.flow_logs_iam_role_arn and log_destination =
# var.flow_logs_destination_arn (providers/aws/primitives/vpc/main.tf:49-55); both are
# REQUIRED inputs the module consumes by ARN and does NOT create itself. Without them the
# CreateFlowLogs API fails: "DeliverLogsPermissionArn can't be empty if LogDestinationType
# is cloud-watch-logs."
#
# WHY A generate BLOCK (no module source):
# The collector leaf sources exactly ONE module (AC-3: no primitive sourced directly from
# the leaf), so it cannot create the flow-logs CloudWatch log group + IAM role inline.
# providers/aws/** is frozen and no released module provisions a flow-logs role+destination
# pair, so this peer dependency unit emits the two resources via a terragrunt generate block
# (matching the cloudfront-logs unit) and exposes their ARNs as outputs the collector leaf
# wires in. Mirrors the collector-ingestion EXAMPLE fixture's flow-logs prerequisite
# (examples/default/main.tf: aws_cloudwatch_log_group.flow_logs + aws_iam_role.flow_logs),
# improved with a least-privilege inline policy so the role can actually deliver logs.
#
# ENCRYPTION (D10/iac/04 I6): the log group is encrypted with the telemetry-data CMK
# (lake_kms_key_arn from the data-lake dependency). That CMK policy already grants
# logs.<region>.amazonaws.com via the ArnLike EncryptionContext condition for every log
# group in this account/region, so no KMS policy change is required (same pattern the
# collector's telemetry ingest + WAF log groups use).
#
# IDENTITY (D31): sandbox and prod are byte-for-byte identical; only the namespace-derived
# log-group name and role name differ per env (namespace-derived-names law). No account id,
# region, or env literal is a static literal in the generated config -- the CMK ARN flows
# from the dependency output and names derive from the namespace.
#
# Applied with the env's deploy credentials. No _envcommon include at this service layer.

locals {
  # service resolves to the directory basename, i.e. "vpc-flow-logs".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())
}

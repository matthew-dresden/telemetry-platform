# terraform.tfvars -- observability deployment-unique values (spec section 4.5, AC-17)
#
# This file is auto-loaded by Terraform after Terragrunt copies the unit directory
# into the module working directory (spec section 1.1). It carries deployment-unique
# fixed values that are specific to this leaf and must not be baked into the shared
# terragrunt.hcl template.
#
# DEPLOYMENT-UNIQUE FIXED VALUES (spec section 0.5, AC-FUNC-002):
#   create_dashboard -- controls whether references/observability provisions the
#   CloudWatch dashboard resource. Value true = provision the dashboard for this
#   sandbox deployment. This value was previously inlined in the leaf terragrunt.hcl
#   inputs block and is now sourced from this file (AC-FUNC-002). It maps to the
#   declared module variable create_dashboard in references/observability
#   (variables.tf), which passes it to the cloudwatch primitive create_dashboard input.
#   To retune dashboard provisioning for this deployment, edit this file only.
#
# Alarm thresholds (metric_name, namespace, statistic, comparison_operator,
# threshold, period, evaluation_periods) are declared in service.hcl as alarm_configs
# and are NOT deployment-unique leaf-inlined values; they are sourced via
# local.service_vars.locals.alarm_configs (AC-FUNC-003).
#
# Budget and cost-anomaly inputs (budget_amount, budget_notification_thresholds,
# budget_subscriber_email_addresses, cost_anomaly_threshold_expression) are sourced
# from service.hcl locals and are NOT moved to tfvars (AC-FUNC-003).
#
# Scope-shared values (emails, domain apexes, CIDR) are NOT present here; they
# remain in common/ and _envcommon/ (spec section 4.5, AC-FUNC-003, AC-FUNC-004).
#
# Secret material is NEVER stored in terraform.tfvars; only ARNs/names and SSM paths
# are referenced (spec section 3.6, AC-FUNC-004).

create_dashboard = true

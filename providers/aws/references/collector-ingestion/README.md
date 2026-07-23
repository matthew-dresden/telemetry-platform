# collector-ingestion

Assembles the full OTLP collector edge-to-service path as a single NEW-LOCAL reference so a
deployment unit can stand up ingestion without hand-wiring nine modules. One call deploys the
complete stack: VPC, ALB, ECS cluster, ADOT service, WAF, CloudFront, ACM validation, and
Route53 record.

## Edge: CloudFront VPC origin (no origin self-loop)

The collector CloudFront distribution reaches the **internal** ALB through a **CloudFront VPC
origin** (`cloudfront-distribution` `origin_type = "vpc"`, `vpc_origin_arn =
module.adot_service.alb_arn`), not a public custom origin. CloudFront routes to the ALB
privately by the VPC origin (the ALB ARN), so the ALB stays `scheme=internal` and there is **no
CloudFront-to-CloudFront origin self-loop** — the distribution origin `domain_name`
(`collector_service_fqdn`) is used only for the Host header / TLS SNI, even though that name is
a Route53 A-alias back to the same distribution. The ALB HTTPS-listener certificate
(`certificate_arn`) must cover `collector_service_fqdn` so the `https-only` origin TLS handshake
validates.

CloudFront origin-facing traffic to the ALB is sourced from the AWS-managed prefix list
`com.amazonaws.global.cloudfront.origin-facing`, so the internal ALB security group allows
inbound from that prefix list (`cloudfront_origin_facing_prefix_list_id`) on the HTTPS listener
port (`alb_https_listener_port`) and nothing else — CloudFront is the single public entry point.
The prefix-list id is an **input** (region-specific AWS-managed value supplied by the terragrunt
leaf), never a `data "aws_ec2_managed_prefix_list"` lookup, so the module performs **zero
plan-time AWS reads**. The viewer DNS chain (`collector.<pretty>` / `collector_service_fqdn`
A-alias → CloudFront) is unchanged; only the origin side uses the VPC origin.

## Composed and REUSED modules

| Module | Version | Role |
|--------|---------|------|
| `vpc-network` (telemetry-platform) | v1.0.0 | VPC, subnets, NAT gateways, route tables, VPC endpoints |
| `ecs-app-cluster` (telemetry-platform) | v1.0.0 | ECS cluster, execution role, CloudWatch log groups |
| `ecs-app-deploy` (telemetry-platform) | v1.0.0 | ADOT ECS service, task role, ALB, ALB listener |
| `cloudfront-distribution` (telemetry-platform) | v1.0.0 | CloudFront distribution with WAF attachment and ACM certificate |
| REUSED `waf-webacl` (telemetry-platform) | v1.1.0 | WAF WebACL (CLOUDFRONT scope) with three managed rule groups (AnonymousIpList omitted -- open-ingestion posture); v1.1.0 per-sub-rule `rule_action_override` neutralizes only `CommonRuleSet`'s `SizeRestrictions_BODY` (BUG-1) |
| REUSED `route53-record` (telemetry-platform) | v1.0.0 | Route53 CNAME record pointing the collector pretty FQDN at the CloudFront distribution domain (R2) |
| `aws_acm_certificate_validation` | built-in | ACM certificate validation only -- no certificate creation |

Each in-repo child module source is a `const=true` variable that defaults to a local relative path
for local development (when `use_pinned_module_sources = false`) and is set to a pinned
`git::...?ref=<path>/v<semver>` URL by the terragrunt leaf in prod environments
(when `use_pinned_module_sources = true`). External `terraform-modules` sources remain pinned literals
at all times. See the table below and `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## Child module source variables (const=true convention)

Each child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `vpc_network_source` | `../vpc-network` | Source for the vpc-network reference module |
| `ecs_cluster_source` | `../ecs-app-cluster` | Source for the ecs-app-cluster reference module |
| `ecs_deploy_source` | `../ecs-app-deploy` | Source for the ecs-app-deploy reference module |
| `waf_webacl_source` | `../../primitives/waf-webacl` | Source for the waf-webacl primitive module |
| `cloudfront_source` | `../../primitives/cloudfront-distribution` | Source for the cloudfront-distribution primitive module |
| `route53_record_source` | `../../primitives/route53-record` | Source for the route53-record primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/collector-ingestion.hcl` to the leaf.
See `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## D37 input contract

Decision D37 breaks the potential output-fed-as-input cycle by declaring six values as INPUTS
instead of computing them locally:

| Input | Description |
|-------|-------------|
| `firehose_delivery_stream_arn` | ARN of the Firehose delivery stream from the data-lake reference. Never a computed output. |
| `collector_service_fqdn` | Per-set collector service FQDN. CloudFront viewer alias AND the VPC-origin `domain_name` (Host header / TLS SNI only -- CloudFront routes to the internal ALB by the VPC origin, not by DNS, so this name being an A-alias to the same distribution does not loop). |
| `collector_pretty_fqdn` | Human-friendly FQDN for the collector endpoint (CloudFront alias, Route53 record). |
| `prod_hosted_zone_id` | Route53 hosted zone ID for the production DNS zone. |
| `certificate_arn` | ACM certificate ARN from the acm-collector module (consumed, never created). |
| `domain_validation_options` | Domain validation options from the ACM certificate (passed to `aws_acm_certificate_validation`). |

## docs/terragrunt-concepts.md runtime inputs (D44 concrete defaults)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `adot_image` | string | (required) | Pinned Docker image URI for the ADOT collector |
| `adot_task_cpu` | number | `1024` | CPU units for the ADOT ECS task -- right-sized for high-concurrency ingestion (was `512`) |
| `adot_task_memory` | number | `2048` | Memory (MiB) for the ADOT ECS task -- right-sized for high-concurrency ingestion (was `1024`) |
| `adot_receiver_max_request_body_size` | number | `4194304` | Maximum OTLP HTTP request body size in bytes (4 MiB, D44) |
| `memory_limiter_limit_mib` | number | `1800` | Hard memory limit (MiB) for the ADOT `memory_limiter` processor (D5), raised proportionally to `adot_task_memory` (was `900`) |
| `memory_limiter_spike_limit_mib` | number | `400` | Soft spike limit (MiB) for the ADOT `memory_limiter` processor (D5), raised proportionally (was `200`) |
| `rate_limit_per_ip` | number | `2000` | WAF rate limit per source IP per 5-minute window (D44) |
| `adot_desired_count` | number | `4` | Baseline/initial desired ADOT ECS task count -- the PRIMARY capacity lever for a known concurrency target (e.g. ~2000 concurrent Claude Code sessions), since ECS scale-out latency is on the order of minutes |
| `adot_enable_autoscaling` | bool | `true` | Whether to enable ECS Application Auto Scaling (CPU target-tracking) for the ADOT service |
| `adot_autoscaling` | object | `{min_capacity=4, max_capacity=12, cpu_target_percent=50}` | CPU target-tracking Application Auto Scaling configuration; `min_capacity` should carry the full expected concurrent load on its own -- autoscaling is the elastic cushion above it and the off-hours scale-in below it |
| `adot_exporter_sending_queue_size` | number | `10000` | `sending_queue.queue_size` for the `awscloudwatchlogs` / `awscloudwatchlogs/structured` exporters |
| `adot_exporter_sending_queue_num_consumers` | number | `8` | `sending_queue.num_consumers` for the `awscloudwatchlogs` / `awscloudwatchlogs/structured` exporters |
| `adot_batch_send_batch_size` | number | `8192` | `send_batch_size` for the shared `batch` processor (logs / logs/structured pipelines) |
| `adot_batch_timeout_seconds` | number | `5` | `timeout` (seconds) for the shared `batch` processor, rendered as `"<n>s"` |
| `vpc_cidr_block` | string | (required) | CIDR block for the composed vpc-network |
| `subnet_layout` | list(object) | (required) | Subnet definitions for the composed vpc-network |
| `nat_gateways` | list(object) | (required) | NAT gateway definitions (name, public_subnet_name, connectivity_type) for the composed vpc-network |
| `interface_endpoint_service_names` | list(string) | (required) | AWS service names for Interface-type VPC endpoints (e.g. `com.amazonaws.us-east-1.ssm`) |
| `interface_endpoint_subnet_names` | list(string) | (required) | Private subnet names to attach to Interface VPC endpoints |
| `s3_gateway_endpoint_service_name` | string | (required) | AWS service name for the S3 Gateway endpoint (e.g. `com.amazonaws.us-east-1.s3`) |
| `adot_container_port` | number | `4318` | Container port the ADOT OTLP/HTTP receiver listens on; used for the ALB target group port and the in-module ADOT task SG ingress |
| `adot_health_check_port` | number | `13133` | Port the ADOT `health_check` extension binds (`0.0.0.0:<port>`) and the ALB target-group health check probes at path `/`; added to the ADOT container portMappings and the ADOT task SG ingress so the internal ALB can reach it |
| `route53_record_ttl` | number | `300` | TTL in seconds for the Route53 CNAME record |
| `waf_log_kms_key_arn` | string | (required) | ARN of the telemetry-data CMK for WAF log encryption |

## Collector WAF managed rule groups (docs/terragrunt-concepts.md)

The REUSED `waf-webacl` module is configured with CLOUDFRONT scope and three AWS managed rule
groups at fixed priorities:

| Priority | Rule Group | Description |
|----------|------------|-------------|
| 10 | `AWSManagedRulesCommonRuleSet` | Common web application protections (`SizeRestrictions_BODY` sub-rule overridden to `count` -- see below) |
| 20 | `AWSManagedRulesKnownBadInputsRuleSet` | Known bad input patterns |
| 30 | `AWSManagedRulesAmazonIpReputationList` | AWS-maintained **known-malicious** IP reputation list |

**Open-ingestion posture.** This is a public telemetry collector whose purpose is to capture
usage data from any tool a company runs (example-cli and others) on any network -- GitHub-hosted,
self-hosted, and cloud CI runners included. `AWSManagedRulesAnonymousIpList` (anonymous/VPN/Tor
and, via `HostingProviderIPList`, all hosting/cloud IPs) is therefore **deliberately omitted**:
it blocks exactly those legitimate emitters (a GitHub Actions runner emit was 403'd solely on
this rule, while the known-malicious `AmazonIpReputationList` blocked nothing legitimate). Only
**known-malicious** IPs are blocked (`AmazonIpReputationList`, p30). Payload attacks
(`CommonRuleSet` SQLi/XSS/NoUserAgent p10 + `KnownBadInputs` p20) and flooding (`RateLimitPerIP`,
p100) stay fully enforced, and the pipeline's own metadata extraction routes any malformed
record to `errors/` (never queryable).

Every group's group-level `override_action` is `none` (fully enforced). The groups are
declared once in `local.waf_managed_rule_groups` (`locals.tf`) -- the single source of truth
that both the `waf_webacl` module block and the `waf_managed_rule_names` /
`waf_managed_rule_groups_echo` outputs derive from (DRY).

### `SizeRestrictions_BODY` sub-rule override (BUG-1: WAF must not block large OTLP bodies)

`AWSManagedRulesCommonRuleSet`'s `SizeRestrictions_BODY` sub-rule returns HTTP `403` for any
request body larger than the WAF default body-inspection limit (~8 KiB; measured ~6 KB -> `200`,
~9 KB -> `403`). Telemetry payloads are legitimately large -- batched / high-volume / large OTLP
log exports run well past 8 KiB, up to the ADOT receiver's `adot_receiver_max_request_body_size`
cap (4 MiB default, D5). Left enforced, this sub-rule silently drops valid telemetry at the WAF
edge before it ever reaches the receiver.

Per AWS guidance, the collector WAF retargets **only** the `SizeRestrictions_BODY` sub-rule to
`count` using the `waf-webacl` v1.1.0 per-sub-rule `rule_action_override` capability:

```hcl
{
  name            = "AWSManagedRulesCommonRuleSet"
  vendor_name     = "AWS"
  priority        = 10
  override_action = "none"           # the group as a whole stays ENFORCED
  rule_action_overrides = [
    {
      name          = "SizeRestrictions_BODY"
      action_to_use = "count"        # oversized bodies are counted, not blocked
    },
  ]
}
```

Every other `CommonRuleSet` rule (SQLi, XSS, LFI, ...) and every other managed group stays fully
enforced -- only this one body-size sub-rule is neutralized. Request-body shaping remains enforced
at the ADOT receiver (D5 `max_request_body_size` + `memory_limiter`), never at WAF.

WAF logging configuration per docs/terragrunt-concepts.md:

- `logging_enabled = true` -- WAF logs are always enabled.
- `log_kms_key_arn = var.waf_log_kms_key_arn` -- logs are encrypted with the telemetry-data CMK
  sourced from the data-lake reference.

Rate limiting is applied via `rate_limit_per_ip` (default 2000 req/5min per D44). This limit acts at the WAF layer before managed rule evaluation.

## D5 ADOT-receiver request shaping

Request shaping lives at the ADOT receiver, not WAF. The AOT_CONFIG_CONTENT value stored in the
SSM SecureString at `/telemetry/<env>/ingest/adot-config` configures three controls:

1. **`max_request_body_size`** -- OTLP HTTP receiver enforces the input value (default 4 MiB,
   D44). Requests exceeding this limit are rejected at the receiver.

2. **`memory_limiter` processor** -- drops load when the collector process exceeds the configured
   memory limits:
   - `limit_mib`: hard limit; traffic refused above this threshold.
   - `spike_limit_mib`: soft limit; throttling begins when memory exceeds `limit_mib - spike_limit_mib`.

3. **Content-type allowlist** -- the OTLP HTTP receiver accepts only:
   - `application/x-protobuf` (OTLP protobuf encoding)
   - `application/json` (OTLP JSON encoding)

### Config-change redeploy trigger

The ADOT config is delivered by-reference (the task reads the SSM SecureString at startup via
the container-definition `secrets` valueFrom), so a config-only change updates SSM without
altering the task definition -- ECS would never roll the service and the running collector
would keep serving the old config. The reference therefore embeds `sha256(adot_config_content)`
as an `ADOT_CONFIG_SHA256` environment variable in every container definition passed to the
`adot_service` module, so any config change produces a new task-definition revision that rolls
the ECS service declaratively (immutable-deploy); the fingerprint is also exposed as the
`adot_config_sha256_echo` output for Terratest assertions.

## ECS scaling and right-sizing (high-concurrency ingestion)

The `adot_service` (`ecs-app-deploy`) call is sized for a high-concurrency ingestion target
(e.g. ~2000 concurrent Claude Code sessions), input-driven end to end so callers can tune it
per environment via terragrunt without a module change:

| Input | Default | Role |
|-------|---------|------|
| `adot_desired_count` | `4` | Initial/baseline ECS task count. **Primary** capacity lever. |
| `adot_enable_autoscaling` | `true` | Enables ECS Application Auto Scaling for the service. |
| `adot_autoscaling` | `{min=4, max=12, cpu_target=50}` | CPU target-tracking Application Auto Scaling range. |
| `adot_task_cpu` | `1024` (was `512`) | Fargate CPU units per task. |
| `adot_task_memory` | `2048` (was `1024`) | Fargate memory (MiB) per task. |
| `memory_limiter_limit_mib` | `1800` (was `900`) | ADOT `memory_limiter` hard cap, raised proportionally to `adot_task_memory`. |
| `memory_limiter_spike_limit_mib` | `400` (was `200`) | ADOT `memory_limiter` soft spike threshold, raised proportionally. |

**The baseline (`adot_desired_count` / `adot_autoscaling.min_capacity`) carries the load, not
scale-out.** ECS Application Auto Scaling reacts on a multi-minute cadence -- a CloudWatch alarm
must breach the target-tracking threshold, the scaling policy must act, and the new task must
pull the image and pass the ALB health check before it serves traffic. That latency makes
scale-out unsuitable as the primary response to a known, steady concurrency target: the running
floor must already carry it. Autoscaling above the baseline is the elastic cushion for bursts
beyond the expected load, and scale-in during off-hours (`adot_autoscaling.max_capacity` is the
ceiling, `scale_in_cooldown` / `scale_out_cooldown` the reaction pacing) is the cost lever below
it -- neither is the sizing mechanism itself.

`adot_desired_count` and `adot_autoscaling` are validated for coherence: when
`adot_enable_autoscaling` is `true`, `adot_desired_count` must fall within
`[adot_autoscaling.min_capacity, adot_autoscaling.max_capacity]`, so the first apply can never
start the service outside the range Application Auto Scaling will subsequently manage it within.
Both inputs are passed straight through to the composed `ecs-app-deploy` (`module.adot_service`)
`desired_count` / `enable_autoscaling` / `autoscaling` inputs, which the underlying `ecs-service`
primitive already implements as CPU target-tracking Application Auto Scaling
(`aws_appautoscaling_target` + `aws_appautoscaling_policy`, `predefined_metric_type =
ECSServiceAverageCPUUtilization`) -- this reference required no downstream module change, only
wiring its own inputs through.

**Deliberately deferred:** request-count-based autoscaling
(`ALBRequestCountPerTarget`) is not wired here. It requires `alb_resource_label` plumbing
through `ecs-app-deploy` (the ALB/target-group resource label the Application Auto Scaling
predefined metric needs) that does not exist yet; CPU target-tracking is the autoscaling
signal this reference implements today.

### ADOT exporter throughput sizing

The `awscloudwatchlogs` and `awscloudwatchlogs/structured` exporters and the shared `batch`
processor in the rendered `AOT_CONFIG_CONTENT` (`locals.tf`) are sized to match the larger
baseline instead of relying on the OTel Collector Contrib built-in defaults:

| Input | Default | Rendered as |
|-------|---------|-------------|
| `adot_exporter_sending_queue_size` | `10000` | `sending_queue.queue_size` on both `awscloudwatchlogs` exporters |
| `adot_exporter_sending_queue_num_consumers` | `8` | `sending_queue.num_consumers` on both `awscloudwatchlogs` exporters |
| `adot_batch_send_batch_size` | `8192` | `processors.batch.send_batch_size` |
| `adot_batch_timeout_seconds` | `5` | `processors.batch.timeout` (rendered `"5s"`) |

`sending_queue` sizing bounds how much delivery backlog each exporter buffers before it applies
backpressure to the OTLP receiver during a CloudWatch Logs delivery slowdown; `batch` sizing
bounds how many OTLP log records are grouped per `PutLogEvents` call. Both were previously
unsized (`sending_queue = { enabled = true }`, `batch = {}`); this reference makes the throughput
budget explicit and input-driven. **Docker-validated** against
`public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1` with the new defaults rendered:
the collector reaches "Everything is ready. Begin running and processing data." (the
`health_check` extension reports `{"status":"Server available",...}` on `GET
http://0.0.0.0:13133/`) with `memory_limiter` logging `limit_mib=1800, spike_limit_mib=400`; an
invalid processors key in the same rendered config makes the collector exit non-zero with `has
invalid keys` (negative control confirming the check discriminates valid from invalid config).

## ADOT ALB health check

The internal ALB target-group health check probes the ADOT `health_check` **extension**, not
the OTLP/HTTP receiver. The OTLP receiver on `adot_container_port` (4318) does not serve a
health path -- it returns HTTP `404` on `/` (and on `/health/status`), which makes the ALB
target report `Target.ResponseCodeMismatch [404]` so the ECS task never becomes healthy and
cycles. The health check therefore targets the `health_check` extension instead:

- **AOT_CONFIG_CONTENT** (`locals.tf`): the `health_check` extension binds
  `endpoint = 0.0.0.0:${var.adot_health_check_port}` (default `0.0.0.0:13133`). The default
  `localhost:13133` binding is loopback-only and unreachable by the ALB, so the endpoint must
  bind all interfaces. `health_check` stays listed in `service.extensions` so it starts.
- **ALB target group** (`main.tf` / `local.adot_target_group_health_check`): `port =
  adot_health_check_port` (13133), `path = "/"`, `matcher = "200"`. The basic `health_check`
  extension returns HTTP `200` on `/` when the pipeline is healthy. Traffic still forwards to
  the OTLP receiver port (`adot_container_port`, 4318) -- only the health probe uses 13133.
- **Container port mapping** (leaf `container_definitions`): `containerPort 13133` (tcp) is
  exposed alongside 4318 so the health port is reachable on the task ENI.
- **ADOT task security group** (`aws_security_group.adot_tasks`): an ingress rule for tcp
  `adot_health_check_port` (13133) from `var.vpc_cidr_block`, alongside the 4318 ingress, so
  the internal ALB can reach the health port.

This is **docker-validated** against
`public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1`: the collector reaches
"Everything is ready. Begin running and processing data.", stays up, and
`GET http://0.0.0.0:13133/` returns HTTP `200` with body
`{"status":"Server available",...}` while the OTLP receiver on 4318 returns `404` on `/`.

## Internal ALB security group (collector-owned)

This reference creates the internal ALB's security group itself
(`aws_security_group.alb`) instead of delegating to the composed `alb` primitive.
The primitive's managed SG performs a **plan-time** `data "aws_vpc"` lookup
(`ec2:DescribeVpcs`) to discover the VPC CIDR for its default egress rule. That is
the only live plan-time AWS read in the module, and it makes a `terragrunt plan`
fail wherever the planning role lacks real `ec2:DescribeVpcs` access (for example
the `dns-owner` cross-account flow that mocks this module's outputs). Because this
reference already builds the VPC (`module.vpc_network`) from `var.vpc_cidr_block`,
it owns the CIDR and creates an equivalent SG with **no** plan-time AWS read.

The `adot_service` (`ecs-app-deploy`) ALB block therefore sets
`create_security_group = false` and passes `alb_security_group_ids =
[aws_security_group.alb.id]`, so the primitive's `data.aws_vpc` `count` evaluates to
`0`. The security posture is byte-for-byte equivalent to the primitive's managed SG:

- **ingress**: TCP `0-65535` from `var.vpc_cidr_block` -- the internal ALB only
  receives traffic from inside the VPC (CloudFront is the public edge).
- **egress**: all protocols to `var.vpc_cidr_block` -- an ALB only forwards to its
  in-VPC registered targets, so VPC-scoped egress is the secure default (the
  primitive resolves the same value from `data.aws_vpc.this[0].cidr_block`).

## D27.3 signal scope (logs primary; structured-OTLP metrics via EMF)

CloudWatch Logs remains the transport for every signal. The AOT_CONFIG_CONTENT declares:

- A `logs` pipeline: `otlp` receiver -> `memory_limiter` + `filter/drop_structured` + `batch`
  processors -> `awscloudwatchlogs` exporter (raw_log=true, byte-unchanged non-structured path).
- A `logs/structured` pipeline: structured-OTLP tool logs, `raw_log=false` (see
  [Structured-OTLP tool telemetry routing](#structured-otlp-tool-telemetry-routing) below).
- A `metrics/structured` pipeline: structured-OTLP tool **metrics**, exported as CloudWatch EMF
  log events (see
  [Structured-OTLP tool telemetry metrics routing](#structured-otlp-tool-telemetry-metrics-routing)
  below) -- this supersedes D27.3's original "no metrics pipeline": the OTLP receiver now
  accepts the metrics signal too. Delivery is still exclusively via CloudWatch Logs.
- Still **no** AMP (Amazon Managed Prometheus) endpoint and **no** `prometheusremotewrite`
  exporter -- metrics are captured as EMF log events, not pushed to a Prometheus-compatible
  backend.
- `/v1/traces` is still unsupported (404).

## Telemetry CloudWatch Logs ingest hop

The `awscloudwatchlogs` ADOT exporter writes each OTLP log record's body string into a
dedicated CloudWatch Logs group, which a match-all subscription filter forwards to the
existing data-lake Firehose. This replaces the prior `otlp/logs` exporter, which pointed
at `firehose.<region>.amazonaws.com` with an `x-amz-firehose-delivery-stream-arn` header --
Firehose does not speak OTLP/HTTP, so that exporter never delivered any record.

The exporter config is **docker-validated** against
`public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1` (the collector logs
"Everything is ready. Begin running and processing data." and stays up; an invalid key
makes it exit non-zero with "has invalid keys").

Path:

1. `awscloudwatchlogs` exporter (`raw_log = true`) writes `log.Body().AsString()` as the
   event message into `aws_cloudwatch_log_group.telemetry_ingest`
   (`/telemetry/<env>/ingest/otlp-logs`, telemetry-data CMK, 1-day retention by default --
   the durable copy lives in the Parquet lake).
2. `aws_cloudwatch_log_subscription_filter` (match-all, `filter_pattern = ""`) forwards every
   record to the existing data-lake Firehose (`var.firehose_delivery_stream_arn`) via
   `aws_iam_role.cwl_to_firehose` (trusts `logs.<region>.amazonaws.com`, grants
   `firehose:PutRecord`/`PutRecordBatch` on the stream ARN only).
3. The Firehose natively decompresses (GZIP) and strips the CloudWatch Logs envelope
   (`CloudWatchLogProcessing` with `DataMessageExtraction = true`) before dynamic
   partitioning and Parquet conversion (see the data-lake reference).

### CWL->Firehose role trust `aws:SourceArn` (BUG-2: 0 records delivered, data lake dark)

The `cwl_to_firehose` role trust policy scopes the assumption to this telemetry ingest log group
with an `ArnLike` `aws:SourceArn` condition. CloudWatch Logs presents `aws:SourceArn` for this role
in **two different forms** depending on the phase, and the condition must allow **both** (verified
empirically in qa -- a single-form pattern breaks one phase):

| Phase | `aws:SourceArn` presented | Symptom if not allowed |
|-------|---------------------------|------------------------|
| **Creation** (`PutSubscriptionFilter` synchronous test message) | **bare** log-group ARN, no `:*` (`arn:aws:logs:<region>:<account>:log-group:/telemetry/<ns>/ingest/otlp-logs`) | `InvalidParameterException: Could not deliver test message ... ACTIVE state` -- the apply fails and the filter is never created |
| **Runtime** (ongoing delivery) | log-group ARN **with the trailing `:*`** (`.../otlp-logs:*`, the canonical form `DescribeLogGroups` returns) | filter exists but Firehose `IncomingRecords = 0`, the S3 `raw/` prefix stays empty, the data lake goes dark even though OTLP POSTs reach the log group |

The Terraform `aws_cloudwatch_log_group.arn` attribute is the **bare** form, so the trust allows
both that bare ARN and that ARN with `:*` appended
(`local.cwl_to_firehose_trust_source_arns = [arn, "${arn}:*"]`). `ArnLike` with a list is an OR,
scoped to exactly this one log group (least privilege). A `:*`-only pattern fails filter creation;
a bare-only pattern (the original bug) silently delivers zero records at runtime. The fix is
asserted by Terratest against the applied state: `cwl_to_firehose_trust_source_arns_echo` must
contain both `telemetry_log_group_arn` and `telemetry_log_group_arn` + `:*`, and the apply
creating the subscription filter is itself proof the creation-time test message was delivered.

`raw_log = true` is **mandatory**: it keeps the SDK event JSON (including the top-level
`.tool` key) intact so the downstream Firehose dynamic-partitioning `MetadataExtractionQuery`
(`tool = .tool`) and the Glue columns (`timestamp`/`tool`/`event_type`/`payload`) populate.
With `raw_log = false` each record would be wrapped as `{body, severity_number, attributes,
resource, scope, ...}`, breaking both the partition key and the column mapping.

The telemetry-data CMK encrypting the ingest group is the same CMK as the WAF log group
(`var.telemetry_log_kms_key_arn`); the data-lake CMK policy already grants
`logs.<region>.amazonaws.com` via the `ArnLike` `kms:EncryptionContext:aws:logs:arn`
condition covering this account/region's log groups, so **no KMS policy change is required**.

The ADOT ECS task role grants `logs:CreateLogStream` + `logs:PutLogEvents` (the
`awscloudwatchlogs` exporters call both) plus `logs:DescribeLogStreams` (the `awsemf`
structured-OTLP metrics exporter additionally calls this at startup -- see
[Structured-OTLP tool telemetry metrics routing](#structured-otlp-tool-telemetry-metrics-routing))
on this group's ARN. The task no longer holds `firehose:PutRecord*` -- the CloudWatch Logs
subscription filter delivers to Firehose via the dedicated `cwl_to_firehose` role.

### Structured-OTLP tool telemetry routing

Structured-OTLP tools (e.g. `claude-code`, `claude-cowork`) emit **structured** OTLP logs --
their usage data (marketplaces/plugins/skills/MCPs) lives in the log record's `attributes`, not
in a flat top-level-`tool` JSON body like example-cli and the other body-contract tools. The
`raw_log = true` path above discards everything except the body string, so it cannot carry
that data.

`var.structured_otlp_service_names` (list of OTLP `resource.attributes["service.name"]` values,
empty by default) routes matching records to a second pipeline, `logs/structured`, whose
`filter/keep_structured` processor keeps only records from those services and exports them via
`awscloudwatchlogs/structured` with `raw_log = false` -- so the full record (`body`,
`attributes`, `resource`, `scope`) is written to CloudWatch Logs and the data-lake `cwl_split`
transform can read the attributes and reshape them into the canonical
`{tool, event_type, payload}` envelope.

The existing `logs` pipeline gains a matching `filter/drop_structured` processor (drops exactly
the records `logs/structured` keeps) but is otherwise **byte-unchanged**: every non-structured
tool still exports via `awscloudwatchlogs` with `raw_log = true`. The keep/drop conditions are
derived from the same `structured_otlp_service_names` list, so the two paths are mutually
exclusive and exhaustive by construction.

With the default `structured_otlp_service_names = []`, the routing is **inert**:
`filter/keep_structured` drops everything (so `logs/structured` never exports) and
`filter/drop_structured` drops nothing (so `logs` behaves exactly as it did before this input
existed).

### Structured-OTLP tool telemetry metrics routing

Structured-OTLP tools also emit OTLP **metrics** (e.g. `claude_code.token.usage`, session/cost
counters). These arrive on the same `otlp` receiver as logs (the OTLP/HTTP receiver serves
both `/v1/logs` and `/v1/metrics`; `/v1/traces` is still unsupported) and are routed by a
dedicated `metrics/structured` pipeline:

1. **`filter/keep_structured_metrics`** (processor, `metrics.datapoint` OTTL context) keeps
   only datapoints from `var.structured_otlp_service_names`. It **reuses
   `local.structured_filter_keep_condition` verbatim** -- the same expression that gates
   `logs/structured` -- because `service.name` is a resource attribute and its OTTL evaluation
   is identical for logs and metrics. With the default empty `structured_otlp_service_names`,
   the condition renders `"true"`, which drops every datapoint: the pipeline is **inert** by
   the same construction as the logs routing.
2. **`memory_limiter/metrics`** + **`batch/metrics`** are separate processor *instances* from
   the logs pipeline's `memory_limiter` / `batch` (same input-driven limit values,
   `var.memory_limiter_limit_mib` / `var.memory_limiter_spike_limit_mib`). This isolation is
   deliberate: a burst of structured-OTLP metrics must never trip a shared limiter and cause it
   to also drop in-flight example-cli/body-contract **log** records.
3. **`awsemf` exporter** converts each kept metric datapoint into a CloudWatch EMF (Embedded
   Metric Format) log event and writes it to a **third** stream,
   `local.structured_metrics_log_stream_name`
   (`adot-collector-metrics-emf`) -- distinct from the raw `adot-collector` stream and
   the structured `adot-collector-structured` stream -- in the **same** telemetry
   ingest log group (`local.telemetry_log_group_name`). No new log group, subscription
   filter, Firehose, or KMS grant is needed: the existing group-scoped match-all subscription
   filter (`aws_cloudwatch_log_subscription_filter.telemetry_ingest_to_firehose`) already
   forwards every stream in the group, and the group is encrypted with the same
   telemetry-data CMK as the other exporters.

The `awsemf` exporter config keys are **docker-validated** against
`public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1` (the collector reaches
"Everything is ready. Begin running and processing data." with the `metrics/structured`
pipeline declared and no config/decode errors):

| Key | Value | Why |
|-----|-------|-----|
| `namespace` | `var.claude_metrics_emf_namespace` (default `"Telemetry/ClaudeCode"`) | Input-driven CloudWatch namespace for the extracted metrics. The variable name and default are kept as-is to avoid a CloudWatch-namespace migration; the namespace now covers every structured-OTLP tool, not only Claude. |
| `log_group_name` | `local.telemetry_log_group_name` | The SAME group the `awscloudwatchlogs` exporters target (single log group for all signals). |
| `log_stream_name` | `local.structured_metrics_log_stream_name` | The dedicated third stream, so the metrics EMF events never contend with the other two streams. |
| `resource_to_telemetry_conversion.enabled` | `true` | **Mandatory.** Without it the EMF event carries no `service.name` field, so the data-lake reshape's service->tool mapping cannot classify the row. |
| `dimension_rollup_option` | `"NoDimensionRollup"` | Combined with `metric_declarations` below, emits exactly one coarse zero-dimension CloudWatch metric per metric name -- the minimal/negligible-cost shape. `awsemf` always emits at least one CloudWatch metric per name (it cannot be suppressed entirely); the durable, full-fidelity data lives in the reshaped Parquet lake row, not in the CloudWatch metric itself. |
| `metric_declarations` | `[{ dimensions = [[]], metric_name_selectors = [".*"] }]` | One declaration matching every metric name with an empty dimension set. |

Two additional semantics carried forward from the docker-validated design (see
`data-lake` module docs for the reshape side):

- `awsemf` converts cumulative monotonic OTLP sums into **per-interval delta** values in the
  EMF payload, so lake rows for these metrics are deltas, not running totals -- downstream
  Athena queries must `SUM` for a total, not `MAX`.
- A single EMF log event can carry multiple datapoints/metric names; the data-lake `cwl_split`
  reshape returns a list of rows per event, not a single row.

The ADOT ECS task role's CloudWatch Logs statement additionally grants
`logs:DescribeLogStreams` (alongside the existing `logs:CreateLogStream` /
`logs:PutLogEvents`) on the same telemetry ingest group ARN -- the `awsemf` exporter calls
`DescribeLogStreams` at startup (beyond what the `awscloudwatchlogs` exporters need), and
without it the exporter fails to start. No `cloudwatch:PutMetricData` grant is added: EMF
metric extraction from the log payload happens server-side in CloudWatch, so the task never
calls `PutMetricData` directly.

With the default `structured_otlp_service_names = []`, `metrics/structured` is **inert** for the
same reason `logs/structured` is: the reused keep condition renders `"true"`, so
`filter/keep_structured_metrics` drops every datapoint and the `awsemf` exporter never writes an
event.

## acm-collector-only certificate rule (docs/terragrunt-concepts.md)

This reference **never** creates an `aws_acm_certificate` resource. ACM certificates are created
exclusively by the `acm-collector` module. This reference:

1. Accepts the pre-issued certificate ARN via `var.certificate_arn` (D37 input).
2. Accepts the domain validation options via `var.domain_validation_options` (D37 input).
3. Creates `aws_acm_certificate_validation` to complete DNS validation using the provided options.

See docs/adr/ D37 for the full rationale.

## Outputs

| Output | Description |
|--------|-------------|
| `cloudfront_domain_name` | CloudFront distribution domain (e.g. `d1234abcdef.cloudfront.net`). Sourced from `module.cloudfront.distribution_domain_name`. |
| `cloudfront_hosted_zone_id` | CloudFront distribution hosted zone ID for Route53 alias records. Sourced from `module.cloudfront.distribution_hosted_zone_id`. |
| `alb_arn` | ARN of the Application Load Balancer fronting the ADOT ECS service. |
| `ecs_service_name` | Name of the ADOT ECS service. |
| `vpc_id` | The ID of the VPC created by the composed vpc-network module. |
| `internet_gateway_id` | The ID of the Internet Gateway created by the composed vpc-network module (public route gateway, docs/terragrunt-concepts.md). |
| `adot_security_group_id` | The ID of the ADOT ECS task security group created in-module (docs/terragrunt-concepts.md). |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | ~> 6.0.0 |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_adot_service"></a> [adot\_service](#module\_adot\_service) | var.ecs\_deploy\_source | n/a |
| <a name="module_cloudfront"></a> [cloudfront](#module\_cloudfront) | var.cloudfront\_source | n/a |
| <a name="module_ecs_cluster"></a> [ecs\_cluster](#module\_ecs\_cluster) | var.ecs\_cluster\_source | n/a |
| <a name="module_route53_record"></a> [route53\_record](#module\_route53\_record) | var.route53\_record\_source | n/a |
| <a name="module_vpc_network"></a> [vpc\_network](#module\_vpc\_network) | var.vpc\_network\_source | n/a |
| <a name="module_waf_webacl"></a> [waf\_webacl](#module\_waf\_webacl) | var.waf\_webacl\_source | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [aws_acm_certificate_validation.collector](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/acm_certificate_validation) | resource |
| [aws_security_group.adot_tasks](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/security_group) | resource |
| [aws_security_group.alb](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/security_group) | resource |
| [aws_ssm_parameter.adot_config](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ssm_parameter) | resource |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_adot_autoscaling"></a> [adot\_autoscaling](#input\_adot\_autoscaling) | (Optional) ECS Application Auto Scaling configuration for the ADOT service, passed straight through to the composed ecs-app-deploy (module.adot\_service) autoscaling input (CPU target-tracking via ECSServiceAverageCPUUtilization). Required (non-null) when adot\_enable\_autoscaling is true. min\_capacity should carry the FULL expected concurrent load on its own (e.g. ~2000 concurrent Claude Code sessions): ECS scale-out latency is on the order of minutes (alarm breach, scaling action, task startup, ALB health check), so the running floor -- not a future scale-out event -- is the primary capacity guarantee; autoscaling above min\_capacity is the elastic cushion for bursts and max\_capacity is the ceiling, not the sizing target. Defaults to a generous baseline: min\_capacity 4, max\_capacity 12, cpu\_target\_percent 50 (scales out earlier than the ecs-app-deploy default of 60 so headroom is reserved before CPU saturates), scale\_in\_cooldown 300s, scale\_out\_cooldown 60s. | <pre>object({<br/>    min_capacity       = number<br/>    max_capacity       = number<br/>    cpu_target_percent = optional(number, 60)<br/>    scale_in_cooldown  = optional(number, 300)<br/>    scale_out_cooldown = optional(number, 60)<br/>  })</pre> | <pre>{<br/>  "cpu_target_percent": 50,<br/>  "max_capacity": 12,<br/>  "min_capacity": 4<br/>}</pre> | no |
| <a name="input_adot_batch_send_batch_size"></a> [adot\_batch\_send\_batch\_size](#input\_adot\_batch\_send\_batch\_size) | (Optional) send\_batch\_size (number of OTLP records grouped per batch before export) for the shared "batch" processor in the rendered AOT\_CONFIG\_CONTENT (the logs and logs/structured pipelines). Larger batches reduce the number of PutLogEvents calls under high-concurrency ingestion. Defaults to 8192. | `number` | `8192` | no |
| <a name="input_adot_batch_timeout_seconds"></a> [adot\_batch\_timeout\_seconds](#input\_adot\_batch\_timeout\_seconds) | (Optional) timeout, in seconds, that bounds how long the shared "batch" processor in the rendered AOT\_CONFIG\_CONTENT waits to fill a batch to adot\_batch\_send\_batch\_size before flushing anyway. Rendered as "<n>s" in the ADOT config. Defaults to 5. | `number` | `5` | no |
| <a name="input_adot_container_port"></a> [adot\_container\_port](#input\_adot\_container\_port) | (Optional) Container port the ADOT collector OTLP/HTTP receiver listens on. Used for the ALB target group port and the in-module ADOT task security group ingress. Defaults to 4318 (OTLP/HTTP). | `number` | `4318` | no |
| <a name="input_adot_desired_count"></a> [adot\_desired\_count](#input\_adot\_desired\_count) | (Optional) Initial/baseline desired task count for the ADOT ECS service, passed straight through to the composed ecs-app-deploy (module.adot\_service) desired\_count input. This is the PRIMARY capacity lever: ECS Application Auto Scaling reacts on a multi-minute cadence (alarm breach -> scaling action -> task startup -> ALB health check), so the baseline count -- not scale-out -- must already carry the expected steady-state concurrent load (e.g. ~2000 concurrent Claude Code sessions). Defaults to 4, a generous baseline sized for that target load; terragrunt sets the real per-env value. When adot\_enable\_autoscaling is true, ECS Application Auto Scaling manages the running count between adot\_autoscaling.min\_capacity and adot\_autoscaling.max\_capacity thereafter, and this value must fall within that range so the initial deployment is coherent with the scaling policy from the first apply. | `number` | `4` | no |
| <a name="input_adot_enable_autoscaling"></a> [adot\_enable\_autoscaling](#input\_adot\_enable\_autoscaling) | (Optional) Whether to enable ECS Application Auto Scaling (CPU target-tracking) for the ADOT service, passed straight through to the composed ecs-app-deploy (module.adot\_service) enable\_autoscaling input -- which the ecs-service primitive already implements via aws\_appautoscaling\_target/aws\_appautoscaling\_policy with predefined\_metric\_type = ECSServiceAverageCPUUtilization. Defaults to true: the collector is a public high-concurrency ingestion edge, so elastic headroom above the adot\_desired\_count baseline (and scale-in during off-hours) is the secure/cost-aware default. When true, adot\_autoscaling must be non-null (enforced by the composed ecs-service primitive's fail-fast precondition). | `bool` | `true` | no |
| <a name="input_adot_exporter_sending_queue_num_consumers"></a> [adot\_exporter\_sending\_queue\_num\_consumers](#input\_adot\_exporter\_sending\_queue\_num\_consumers) | (Optional) sending\_queue.num\_consumers (parallel goroutines draining the sending queue to CloudWatch Logs) for the awscloudwatchlogs and awscloudwatchlogs/structured exporters in the rendered AOT\_CONFIG\_CONTENT. Higher concurrency drains the queue faster under high-concurrency ingestion. Defaults to 8. | `number` | `8` | no |
| <a name="input_adot_exporter_sending_queue_size"></a> [adot\_exporter\_sending\_queue\_size](#input\_adot\_exporter\_sending\_queue\_size) | (Optional) sending\_queue.queue\_size (buffered batches awaiting delivery) for the awscloudwatchlogs and awscloudwatchlogs/structured exporters in the rendered AOT\_CONFIG\_CONTENT. Sized for high-concurrency ingestion so a delivery slowdown does not immediately apply backpressure to the OTLP receiver. Defaults to 10000. | `number` | `10000` | no |
| <a name="input_adot_health_check_port"></a> [adot\_health\_check\_port](#input\_adot\_health\_check\_port) | (Optional) TCP port the ADOT collector health\_check extension binds (0.0.0.0:<port>) and the ALB target-group health check probes on path "/". The OTLP/HTTP receiver (adot\_container\_port) does not serve a health path, so the ALB must probe the health\_check extension's port instead -- otherwise the target reports Target.ResponseCodeMismatch [404] and the ECS task cycles. The basic health\_check extension serves HTTP 200 on "/" when the pipeline is healthy. Added to the ADOT container portMappings and the in-module ADOT task security group ingress so the internal ALB can reach it. Defaults to 13133 (the ADOT health\_check extension default port). | `number` | `13133` | no |
| <a name="input_adot_image"></a> [adot\_image](#input\_adot\_image) | (Required) Docker image URI for the ADOT collector (e.g. public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1). Must be pinned to a specific digest or tag. | `string` | n/a | yes |
| <a name="input_adot_receiver_max_request_body_size"></a> [adot\_receiver\_max\_request\_body\_size](#input\_adot\_receiver\_max\_request\_body\_size) | (Optional) Maximum OTLP HTTP request body size in bytes enforced by the ADOT receiver (D5). Shipped into the AOT\_CONFIG\_CONTENT SSM SecureString. Concrete default 4194304 bytes (4 MiB) per D44. | `number` | `4194304` | no |
| <a name="input_adot_task_cpu"></a> [adot\_task\_cpu](#input\_adot\_task\_cpu) | (Optional) CPU units for the ADOT ECS task. Must be a valid Fargate CPU value. Defaults to 1024 -- right-sized (from the prior 512 D44 default) for the ~2000-concurrent-session high-concurrency ingestion target; terragrunt sets the real per-env value. | `number` | `1024` | no |
| <a name="input_adot_task_memory"></a> [adot\_task\_memory](#input\_adot\_task\_memory) | (Optional) Memory (MiB) for the ADOT ECS task. Must be a valid Fargate combination. Defaults to 2048 -- right-sized (from the prior 1024 D44 default) for the ~2000-concurrent-session high-concurrency ingestion target; terragrunt sets the real per-env value. Must exceed memory\_limiter\_limit\_mib (the memory\_limiter processor's hard cap must leave headroom below the task's hard OOM limit). | `number` | `2048` | no |
| <a name="input_alb_https_listener_port"></a> [alb\_https\_listener\_port](#input\_alb\_https\_listener\_port) | (Optional) TCP port of the internal ALB HTTPS listener that CloudFront reaches via the VPC origin. Single source of truth wired into the ALB HTTPS listener, the CloudFront VPC origin endpoint https\_port, and the internal ALB security-group ingress rule. Defaults to 443. | `number` | `443` | no |
| <a name="input_alb_name"></a> [alb\_name](#input\_alb\_name) | (Required) Name for the Application Load Balancer. | `string` | n/a | yes |
| <a name="input_certificate_arn"></a> [certificate\_arn](#input\_certificate\_arn) | (Required) ARN of the ACM certificate issued by the acm-collector module. This reference performs certificate VALIDATION only -- per docs/terragrunt-concepts.md, certificates are created exclusively by the acm-collector module. Declared as an INPUT per D37. | `string` | n/a | yes |
| <a name="input_claude_metrics_emf_namespace"></a> [claude\_metrics\_emf\_namespace](#input\_claude\_metrics\_emf\_namespace) | (Optional) CloudWatch namespace the awsemf exporter publishes structured-OTLP tool metrics under (the metrics/structured pipeline). The pipeline is gated by the SAME structured\_otlp\_service\_names input as the logs/structured pipeline (empty is inert -- the keep-condition drops every datapoint), so this namespace value is inert too until structured\_otlp\_service\_names is non-empty. Defaults to "Telemetry/ClaudeCode" -- the variable name and default are kept as-is to avoid a CloudWatch-namespace migration; the namespace now covers every structured-OTLP tool, not only Claude. | `string` | `"Telemetry/ClaudeCode"` | no |
| <a name="input_cloudfront_origin_facing_prefix_list_id"></a> [cloudfront\_origin\_facing\_prefix\_list\_id](#input\_cloudfront\_origin\_facing\_prefix\_list\_id) | (Required) ID of the AWS-managed CloudFront origin-facing prefix list (com.amazonaws.global.cloudfront.origin-facing) for this region. Allowed as inbound on the internal ALB security group (HTTPS listener port) so CloudFront can reach the ALB via the VPC origin. Region-specific AWS-managed value (e.g. pl-3b927c52 in us-east-1), supplied as an input so the module performs no plan-time AWS read. | `string` | n/a | yes |
| <a name="input_cluster_name"></a> [cluster\_name](#input\_cluster\_name) | (Required) Name of the ECS cluster to create for the ADOT service. | `string` | n/a | yes |
| <a name="input_collector_pretty_fqdn"></a> [collector\_pretty\_fqdn](#input\_collector\_pretty\_fqdn) | (Required) Human-friendly FQDN for the collector endpoint (e.g. collector.example.com). Used as the CloudFront alias and route53-record value. Declared as an INPUT per D37. | `string` | n/a | yes |
| <a name="input_collector_service_fqdn"></a> [collector\_service\_fqdn](#input\_collector\_service\_fqdn) | (Required) Per-set service FQDN of the collector. Attached as a CloudFront viewer alias AND used as the CloudFront VPC-origin domain\_name. With a VPC origin, CloudFront routes to the internal ALB privately by the VPC origin (the ALB ARN), NOT by public DNS of this name, so the fact that this name is a Route53 A-alias to the same distribution does NOT create an origin self-loop; it is used only for the Host header and TLS SNI/cert validation, so the ALB HTTPS-listener certificate must cover it. Declared as an INPUT per D37. | `string` | n/a | yes |
| <a name="input_container_definitions"></a> [container\_definitions](#input\_container\_definitions) | (Required) JSON-encoded list of container definitions for the ADOT ECS task. | `string` | n/a | yes |
| <a name="input_cwl_to_firehose_role_name"></a> [cwl\_to\_firehose\_role\_name](#input\_cwl\_to\_firehose\_role\_name) | (Required) Name of the IAM role CloudWatch Logs assumes to deliver subscription-filter records to the data-lake Firehose. Trusts logs.<region>.amazonaws.com and grants firehose:PutRecord/PutRecordBatch on firehose\_delivery\_stream\_arn only. SEPARATE from the ADOT task role and the Firehose delivery role. Must be 1-64 characters (IAM role name limit). | `string` | n/a | yes |
| <a name="input_domain_validation_options"></a> [domain\_validation\_options](#input\_domain\_validation\_options) | (Required) List of domain validation options from the ACM certificate. Passed to aws\_acm\_certificate\_validation. Declared as an INPUT per D37 -- this reference never creates certificates. | <pre>list(object({<br/>    domain_name           = string<br/>    resource_record_name  = string<br/>    resource_record_type  = string<br/>    resource_record_value = string<br/>  }))</pre> | n/a | yes |
| <a name="input_enable_flow_logs"></a> [enable\_flow\_logs](#input\_enable\_flow\_logs) | (Optional) Whether to enable VPC Flow Logs for the composed vpc-network module. Defaults to false. | `bool` | `false` | no |
| <a name="input_env"></a> [env](#input\_env) | (Required) Environment name (e.g. sandbox, qa, prod). Used to construct the ADOT SSM parameter path /telemetry/<env>/ingest/adot-config. | `string` | n/a | yes |
| <a name="input_execution_role_assume_policy_json"></a> [execution\_role\_assume\_policy\_json](#input\_execution\_role\_assume\_policy\_json) | (Required) Trust policy JSON for the ECS task execution role. | `string` | n/a | yes |
| <a name="input_execution_role_name"></a> [execution\_role\_name](#input\_execution\_role\_name) | (Required) Name of the ECS task execution IAM role. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_firehose_delivery_stream_arn"></a> [firehose\_delivery\_stream\_arn](#input\_firehose\_delivery\_stream\_arn) | (Required) ARN of the Firehose delivery stream sourced from the data-lake reference output. Declared as an INPUT per D37 -- never a computed output of this reference. | `string` | n/a | yes |
| <a name="input_interface_endpoint_service_names"></a> [interface\_endpoint\_service\_names](#input\_interface\_endpoint\_service\_names) | (Required) List of AWS service names for Interface-type VPC endpoints (e.g. com.amazonaws.us-east-1.ssm). Passed to the composed vpc-network module. | `list(string)` | n/a | yes |
| <a name="input_interface_endpoint_subnet_names"></a> [interface\_endpoint\_subnet\_names](#input\_interface\_endpoint\_subnet\_names) | (Required) List of private subnet names to attach to Interface endpoints. Passed to the composed vpc-network module. | `list(string)` | n/a | yes |
| <a name="input_log_group_name"></a> [log\_group\_name](#input\_log\_group\_name) | (Required) CloudWatch log group name for ADOT container logs. | `string` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_memory_limiter_limit_mib"></a> [memory\_limiter\_limit\_mib](#input\_memory\_limiter\_limit\_mib) | (Optional) Hard memory limit (MiB) for the ADOT memory\_limiter processor (D5). Traffic is dropped when memory exceeds this threshold. Must be less than adot\_task\_memory. Defaults to 1800 -- raised proportionally (from the prior 900 D44 default) for the adot\_task\_memory 2048 MiB right-sized default, leaving headroom below the task's hard Fargate OOM limit. | `number` | `1800` | no |
| <a name="input_memory_limiter_spike_limit_mib"></a> [memory\_limiter\_spike\_limit\_mib](#input\_memory\_limiter\_spike\_limit\_mib) | (Optional) Soft spike limit (MiB) for the ADOT memory\_limiter processor (D5). The processor starts throttling when memory exceeds (limit\_mib - spike\_limit\_mib). Must be less than memory\_limiter\_limit\_mib. Defaults to 400 -- raised proportionally (from the prior 200 D44 default) alongside memory\_limiter\_limit\_mib for the larger 2048 MiB right-sized task. | `number` | `400` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying this reference module. | `string` | `"collector-ingestion"` | no |
| <a name="input_nat_gateways"></a> [nat\_gateways](#input\_nat\_gateways) | (Required) List of NAT gateway definitions for the composed vpc-network module. One entry per AZ for HA (e.g. one per public subnet). | <pre>list(object({<br/>    name               = string<br/>    public_subnet_name = string<br/>    connectivity_type  = optional(string, "public")<br/>  }))</pre> | n/a | yes |
| <a name="input_prod_hosted_zone_id"></a> [prod\_hosted\_zone\_id](#input\_prod\_hosted\_zone\_id) | (Required) Route53 hosted zone ID of the production DNS zone. Declared as an INPUT per D37. | `string` | n/a | yes |
| <a name="input_rate_limit_per_ip"></a> [rate\_limit\_per\_ip](#input\_rate\_limit\_per\_ip) | (Optional) WAF rate limit per source IP per 5-minute window (D44). Applied to the REUSED waf-webacl module. Concrete default 2000 per D44. | `number` | `2000` | no |
| <a name="input_route53_record_ttl"></a> [route53\_record\_ttl](#input\_route53\_record\_ttl) | (Optional) TTL in seconds for the Route53 CNAME record pointing the collector pretty FQDN to the CloudFront distribution domain. Defaults to 300 seconds. | `number` | `300` | no |
| <a name="input_s3_gateway_endpoint_service_name"></a> [s3\_gateway\_endpoint\_service\_name](#input\_s3\_gateway\_endpoint\_service\_name) | (Required) AWS service name for the S3 Gateway endpoint (e.g. com.amazonaws.us-east-1.s3). Passed to the composed vpc-network module. | `string` | n/a | yes |
| <a name="input_service_name"></a> [service\_name](#input\_service\_name) | (Required) Name of the ADOT ECS service. | `string` | n/a | yes |
| <a name="input_structured_otlp_service_names"></a> [structured\_otlp\_service\_names](#input\_structured\_otlp\_service\_names) | (Optional) OTLP resource.service.name values (e.g. claude-code, claude-cowork, claude-office) whose STRUCTURED log records are routed to a second logs pipeline exported with raw\_log=false, so the log-record attributes (marketplaces/plugins/skills/MCPs usage) survive for the data-lake cwl\_split transform to reshape. Every other service (example-cli and any flat-body top-level-'tool' contract tool) stays on the existing raw\_log=true path, byte-unchanged. Empty (the default) is inert: the collector behaves exactly as today until an env supplies these names. The data-lake module's service\_tool\_map must map each of these to a 'tool' partition value. | `list(string)` | `[]` | no |
| <a name="input_subnet_layout"></a> [subnet\_layout](#input\_subnet\_layout) | (Required) List of subnet definitions for the composed vpc-network module. Each entry must specify name, cidr\_block, availability\_zone, and whether it is public. | <pre>list(object({<br/>    name              = string<br/>    cidr_block        = string<br/>    availability_zone = string<br/>    public            = bool<br/>  }))</pre> | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this reference module. | `map(string)` | `{}` | no |
| <a name="input_task_role_assume_policy_json"></a> [task\_role\_assume\_policy\_json](#input\_task\_role\_assume\_policy\_json) | (Required) Trust policy JSON for the ECS task role. | `string` | n/a | yes |
| <a name="input_task_role_inline_policies"></a> [task\_role\_inline\_policies](#input\_task\_role\_inline\_policies) | (Optional) Map of inline policy name to JSON document for the ADOT task role. | `map(string)` | `{}` | no |
| <a name="input_task_role_name"></a> [task\_role\_name](#input\_task\_role\_name) | (Required) Name of the ECS task IAM role for the ADOT service. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_telemetry_log_kms_key_arn"></a> [telemetry\_log\_kms\_key\_arn](#input\_telemetry\_log\_kms\_key\_arn) | (Required) ARN of the telemetry-data KMS CMK used to encrypt the dedicated telemetry CloudWatch Logs ingest group (/telemetry/<env>/ingest/otlp-logs). Set to the telemetry-data CMK ARN sourced from the data-lake reference (the same CMK as waf\_log\_kms\_key\_arn). The data-lake CMK policy already grants logs.<region>.amazonaws.com via the ArnLike kms:EncryptionContext:aws:logs:arn condition. | `string` | n/a | yes |
| <a name="input_telemetry_log_retention_in_days"></a> [telemetry\_log\_retention\_in\_days](#input\_telemetry\_log\_retention\_in\_days) | (Optional) Retention in days for the dedicated telemetry CloudWatch Logs ingest group. The durable copy lives in the Parquet data lake, so this is a transient hop; defaults to 1 for cost mitigation. Also passed to the awscloudwatchlogs exporter log\_retention. Must be a valid CloudWatch retention value. | `number` | `1` | no |
| <a name="input_vpc_cidr_block"></a> [vpc\_cidr\_block](#input\_vpc\_cidr\_block) | (Required) CIDR block for the VPC created by the composed vpc-network module. | `string` | n/a | yes |
| <a name="input_vpc_name"></a> [vpc\_name](#input\_vpc\_name) | (Required) Name for the VPC resource. | `string` | n/a | yes |
| <a name="input_waf_log_kms_key_arn"></a> [waf\_log\_kms\_key\_arn](#input\_waf\_log\_kms\_key\_arn) | (Required) ARN of the telemetry-data KMS CMK used to encrypt WAF logs (docs/terragrunt-concepts.md). Must be set to the telemetry-data CMK ARN sourced from the data-lake reference. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_adot_autoscaling_echo"></a> [adot\_autoscaling\_echo](#output\_adot\_autoscaling\_echo) | JSON-encoded echo of the adot\_autoscaling input (min\_capacity, max\_capacity, cpu\_target\_percent, scale\_in\_cooldown, scale\_out\_cooldown). Exposed for Terratest assertions against the applied aws\_appautoscaling\_target / aws\_appautoscaling\_policy state. |
| <a name="output_adot_config_content_echo"></a> [adot\_config\_content\_echo](#output\_adot\_config\_content\_echo) | Rendered ADOT AOT\_CONFIG\_CONTENT YAML. Exposed for Terratest D5 (memory\_limiter, content-type, max\_request\_body\_size), D27.3 (logs pipeline plus the metrics/structured EMF pipeline), and structured-OTLP logs/metrics routing (filter/keep\_structured, filter/keep\_structured\_metrics) assertions. |
| <a name="output_adot_desired_count_echo"></a> [adot\_desired\_count\_echo](#output\_adot\_desired\_count\_echo) | Echo of the adot\_desired\_count input (the baseline/initial ECS desired task count, the PRIMARY capacity lever). Exposed for Terratest assertions against the applied ECS service DesiredCount. |
| <a name="output_adot_enable_autoscaling_echo"></a> [adot\_enable\_autoscaling\_echo](#output\_adot\_enable\_autoscaling\_echo) | Echo of the adot\_enable\_autoscaling input. Exposed for Terratest assertions that CPU target-tracking Application Auto Scaling is enabled by default. |
| <a name="output_adot_health_check_port_echo"></a> [adot\_health\_check\_port\_echo](#output\_adot\_health\_check\_port\_echo) | Echo of adot\_health\_check\_port. The ALB target-group health check port and the ADOT health\_check extension bind port (0.0.0.0:<port>). Default 13133. |
| <a name="output_adot_security_group_id"></a> [adot\_security\_group\_id](#output\_adot\_security\_group\_id) | The ID of the ADOT ECS task security group created in-module (docs/terragrunt-concepts.md). Attached to the composed vpc-network VPC; egress-all with OTLP/HTTP ingress on the collector container port from inside the VPC. |
| <a name="output_adot_security_group_ingress_ports_echo"></a> [adot\_security\_group\_ingress\_ports\_echo](#output\_adot\_security\_group\_ingress\_ports\_echo) | Comma-separated sorted list of the TCP ingress from\_port values on the in-module ADOT task security group. Must include both adot\_container\_port (4318 OTLP/HTTP) and adot\_health\_check\_port (13133 health check) so the internal ALB can reach both. |
| <a name="output_adot_target_group_health_check_matcher_echo"></a> [adot\_target\_group\_health\_check\_matcher\_echo](#output\_adot\_target\_group\_health\_check\_matcher\_echo) | Echo of the ALB target-group health check success matcher. Must be "200" (the basic health\_check extension returns 200 on "/" when healthy). |
| <a name="output_adot_target_group_health_check_path_echo"></a> [adot\_target\_group\_health\_check\_path\_echo](#output\_adot\_target\_group\_health\_check\_path\_echo) | Echo of the ALB target-group health check path. Must be "/" (the basic ADOT health\_check extension serves 200 on "/"), never the OTLP receiver path /health/status. |
| <a name="output_adot_target_group_health_check_port_echo"></a> [adot\_target\_group\_health\_check\_port\_echo](#output\_adot\_target\_group\_health\_check\_port\_echo) | Echo of the ALB target-group health check port. Must equal adot\_health\_check\_port (13133), never "traffic-port" (which resolves to the 4318 OTLP target port). |
| <a name="output_alb_arn"></a> [alb\_arn](#output\_alb\_arn) | ARN of the Application Load Balancer fronting the ADOT ECS service. |
| <a name="output_cloudfront_domain_name"></a> [cloudfront\_domain\_name](#output\_cloudfront\_domain\_name) | CloudFront distribution domain name (e.g. d1234abcdef.cloudfront.net). Sourced unprefixed from module.cloudfront.distribution\_domain\_name per AC-1. |
| <a name="output_cloudfront_hosted_zone_id"></a> [cloudfront\_hosted\_zone\_id](#output\_cloudfront\_hosted\_zone\_id) | CloudFront distribution hosted zone ID for Route53 alias records. Sourced unprefixed from module.cloudfront.distribution\_hosted\_zone\_id per AC-1. |
| <a name="output_cloudfront_vpc_origin_id"></a> [cloudfront\_vpc\_origin\_id](#output\_cloudfront\_vpc\_origin\_id) | ID of the CloudFront VPC origin the distribution uses to reach the internal ALB privately (origin\_type = vpc). A null value would indicate a regression back to a public custom origin (the self-loop). |
| <a name="output_cwl_to_firehose_role_arn"></a> [cwl\_to\_firehose\_role\_arn](#output\_cwl\_to\_firehose\_role\_arn) | ARN of the IAM role CloudWatch Logs assumes to deliver the telemetry ingest group's subscription-filter records to the data-lake Firehose. |
| <a name="output_cwl_to_firehose_trust_source_arns_echo"></a> [cwl\_to\_firehose\_trust\_source\_arns\_echo](#output\_cwl\_to\_firehose\_trust\_source\_arns\_echo) | JSON-encoded list of the aws:SourceArn values in the CWL-to-Firehose role trust policy ArnLike condition. Must contain BOTH the bare telemetry ingest log-group ARN (the form CloudWatch Logs presents during PutSubscriptionFilter's creation-time test-message delivery) AND that ARN with the trailing ':*' segment (the form presented during runtime delivery), so the subscription filter both CREATES and DELIVERS to Firehose. Exposed for Terratest assertions (BUG-2). |
| <a name="output_ecs_service_name"></a> [ecs\_service\_name](#output\_ecs\_service\_name) | Name of the ADOT ECS service. |
| <a name="output_firehose_delivery_stream_arn_echo"></a> [firehose\_delivery\_stream\_arn\_echo](#output\_firehose\_delivery\_stream\_arn\_echo) | Echo of the firehose\_delivery\_stream\_arn input (D37 contract assertion: this must be an input, never a computed output). |
| <a name="output_internet_gateway_id"></a> [internet\_gateway\_id](#output\_internet\_gateway\_id) | The ID of the Internet Gateway created by the composed vpc-network module (public route gateway, docs/terragrunt-concepts.md). |
| <a name="output_telemetry_log_group_arn"></a> [telemetry\_log\_group\_arn](#output\_telemetry\_log\_group\_arn) | ARN of the dedicated telemetry CloudWatch Logs ingest group. Scoped (with a :* suffix) in the ADOT task role inline policy logs:CreateLogStream/PutLogEvents/DescribeLogStreams statement (DescribeLogStreams is required by the awsemf structured-OTLP metrics exporter). |
| <a name="output_telemetry_log_group_name"></a> [telemetry\_log\_group\_name](#output\_telemetry\_log\_group\_name) | Name of the dedicated telemetry CloudWatch Logs ingest group that the awscloudwatchlogs ADOT exporter writes to and the subscription filter forwards to the data-lake Firehose. |
| <a name="output_telemetry_subscription_filter_destination_arn"></a> [telemetry\_subscription\_filter\_destination\_arn](#output\_telemetry\_subscription\_filter\_destination\_arn) | Echo of the subscription filter destination (the data-lake Firehose ARN). Exposed for Terratest assertions confirming the match-all filter targets the existing Firehose. |
| <a name="output_vpc_id"></a> [vpc\_id](#output\_vpc\_id) | The ID of the VPC created by the composed vpc-network module. |
| <a name="output_waf_log_kms_key_arn_echo"></a> [waf\_log\_kms\_key\_arn\_echo](#output\_waf\_log\_kms\_key\_arn\_echo) | Echo of the waf\_log\_kms\_key\_arn input (telemetry-data CMK). Exposed for Terratest docs/terragrunt-concepts.md assertions. |
| <a name="output_waf_logging_enabled_echo"></a> [waf\_logging\_enabled\_echo](#output\_waf\_logging\_enabled\_echo) | Echo of the WAF logging\_enabled flag (always true per docs/terragrunt-concepts.md). Exposed for Terratest assertions. |
| <a name="output_waf_managed_rule_groups_echo"></a> [waf\_managed\_rule\_groups\_echo](#output\_waf\_managed\_rule\_groups\_echo) | JSON-encoded WAF managed rule groups configuration (name, vendor\_name, priority, group-level override\_action, and per-sub-rule rule\_action\_overrides). Exposed for Terratest assertions that the AWSManagedRulesCommonRuleSet SizeRestrictions\_BODY sub-rule is overridden to count -- so large OTLP request bodies (up to the 4 MiB ADOT receiver cap, D5) are counted rather than 403'd -- while every group's override\_action stays "none" (every other managed rule enforced). docs/terragrunt-concepts.md, BUG-1. |
| <a name="output_waf_managed_rule_names"></a> [waf\_managed\_rule\_names](#output\_waf\_managed\_rule\_names) | Comma-separated list of WAF managed rule group names in priority order. Exposed for Terratest docs/terragrunt-concepts.md assertions. |
| <a name="output_waf_rate_limit_per_ip_echo"></a> [waf\_rate\_limit\_per\_ip\_echo](#output\_waf\_rate\_limit\_per\_ip\_echo) | Echo of the rate\_limit\_per\_ip input value. Exposed for Terratest D44 concrete-value assertions. |
<!-- END_TF_DOCS -->

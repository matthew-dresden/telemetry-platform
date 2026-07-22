locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # ---------------------------------------------------------------------------
  # Dedicated telemetry CloudWatch Logs ingest group (single source of truth, DRY).
  # The awscloudwatchlogs exporter writes here AND the aws_cloudwatch_log_group /
  # aws_cloudwatch_log_stream / aws_cloudwatch_log_subscription_filter in main.tf
  # target the SAME name, so the exporter target and the created group never drift.
  # Name is input-driven from var.env (e.g. /telemetry/sandbox/ingest/otlp-logs).
  # ---------------------------------------------------------------------------
  telemetry_log_group_name  = "/telemetry/${var.namespace}/ingest/otlp-logs"
  telemetry_log_stream_name = "adot-collector"
  # AWS provider 6.x: data.aws_region.current.name is deprecated in favour of .region.
  region = data.aws_region.current.region

  # ---------------------------------------------------------------------------
  # Structured-OTLP telemetry routing (input-driven, tool-registry-agnostic).
  # var.structured_otlp_service_names is the set of OTLP resource.service.name values (e.g.
  # claude-code / claude-cowork / claude-office) whose STRUCTURED log records must be exported
  # with raw_log=false so the log-record ATTRIBUTES survive to CloudWatch Logs (structured-OTLP
  # tools carry their data -- marketplaces/plugins/skills/MCPs -- in attributes, not in the
  # body). The data-lake cwl_split transform then reshapes those into the canonical
  # {tool,event_type,payload} envelope. Every OTHER service (example-cli and any tool that follows the
  # flat-body top-level-`tool` contract) keeps the existing raw_log=true path BYTE-UNCHANGED. The
  # keep/drop filter conditions are derived from the SAME list, so the structured and
  # non-structured paths are MUTUALLY EXCLUSIVE and EXHAUSTIVE by construction -- no record is
  # exported twice and no non-structured record can reach the raw_log=false path.
  #
  # OTTL filter semantics: the `filter` processor DROPS a log record when its condition is TRUE.
  #   - keep_structured (structured pipeline): DROP records that are NOT structured -> AND of
  #     `!=` over the list. A structured record makes one `!=` false, so the AND is false -> kept.
  #   - drop_structured (existing pipeline): DROP records that ARE structured -> OR of `==` over
  #     the list.
  # Empty list (the default) is INERT: keep_structured condition = "true" (structured pipeline
  # drops everything) and drop_structured condition = "false" (existing pipeline drops nothing),
  # so the collector behaves exactly as it does today until an env supplies the structured
  # service names.
  structured_log_stream_name = "${local.telemetry_log_stream_name}-structured"
  structured_filter_keep_condition = length(var.structured_otlp_service_names) > 0 ? join(" and ", [
    for s in var.structured_otlp_service_names : "resource.attributes[\"service.name\"] != \"${s}\""
  ]) : "true"
  structured_filter_drop_condition = length(var.structured_otlp_service_names) > 0 ? join(" or ", [
    for s in var.structured_otlp_service_names : "resource.attributes[\"service.name\"] == \"${s}\""
  ]) : "false"

  # ---------------------------------------------------------------------------
  # Structured-OTLP telemetry metrics routing (mirrors the logs routing above).
  # A THIRD stream, distinct from the raw awscloudwatchlogs stream (telemetry_log_stream_name)
  # and the structured LOGS stream (structured_log_stream_name), in the SAME telemetry ingest
  # log group. The metrics/structured pipeline (below) reuses
  # local.structured_filter_keep_condition verbatim -- it is valid OTTL in the metrics
  # `datapoint` context because service.name is a RESOURCE attribute (not a log-specific field),
  # so the SAME input-driven condition that gates the logs/structured pipeline also gates
  # metrics/structured: empty structured_otlp_service_names renders "true", which drops every
  # datapoint, so the metrics pipeline is fully INERT until an env supplies
  # structured_otlp_service_names (identical inertness contract to the logs routing).
  # ---------------------------------------------------------------------------
  structured_metrics_log_stream_name = "${local.telemetry_log_stream_name}-metrics-emf"

  # ---------------------------------------------------------------------------
  # ADOT AOT_CONFIG_CONTENT rendered from input-driven values.
  # This YAML is stored in the SSM SecureString at
  # /telemetry/<env>/ingest/adot-config and consumed by the ECS task at runtime.
  #
  # D5: OTLP HTTP receiver enforces max_request_body_size and the content-type
  #     allowlist (application/x-protobuf, application/json).
  # D5: memory_limiter processor drops traffic above the configured limits.
  # D27.3 (superseded for structured-OTLP metrics): logs remain the primary transport for every
  #     signal, including metrics -- there is still no AMP endpoint / prometheusremotewrite
  #     exporter. The metrics/structured pipeline below accepts the OTLP metrics signal and
  #     delivers it as CloudWatch EMF log events (awsemf exporter) into the SAME telemetry
  #     ingest log group, so "logs is the transport" still holds; only the RECEIVER now also
  #     accepts metrics (still no /v1/traces support).
  #
  # LOGS EXPORTER (awscloudwatchlogs): the prior otlp/logs exporter pointed at
  # firehose.<region>.amazonaws.com with an x-amz-firehose-delivery-stream-arn
  # header. Firehose does not speak OTLP/HTTP, so that exporter never delivered any
  # record. It is replaced by the awscloudwatchlogs exporter, which writes each OTLP
  # log record's body string (raw_log = true) into the dedicated telemetry CloudWatch
  # Logs group. A match-all subscription filter on that group (main.tf) forwards the
  # records to the existing data-lake Firehose, which natively decompresses and strips
  # the CloudWatch Logs envelope before Parquet conversion. This exporter config is
  # DOCKER-VALIDATED against public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1
  # (the collector logs "Everything is ready. Begin running and processing data." and
  # stays up; an invalid key makes it exit rc=1 with "has invalid keys").
  #
  # raw_log = true is MANDATORY: it writes log.Body().AsString() as the CWL event
  # message (the flat SDK event JSON), keeping .tool a top-level key so the downstream
  # Firehose dynamic-partitioning MetadataExtractionQuery (tool=.tool) and the Glue
  # columns (timestamp/tool/event_type/payload) populate. raw_log = false would wrap
  # each record as {body, severity_number, attributes, resource, scope, ...}, which
  # breaks both the partition key and the column mapping.
  # ---------------------------------------------------------------------------
  adot_config_content = yamlencode({
    extensions = {
      # The health_check extension MUST bind 0.0.0.0 (not the default localhost:13133)
      # so the internal ALB target-group health check can reach it on the task ENI.
      # localhost:13133 is loopback-only and unreachable by the ALB, so the target would
      # report Target.ResponseCodeMismatch / connection refused and the ECS task cycles.
      # The basic health_check extension serves HTTP 200 on "/" when the pipeline is
      # healthy; the ALB target group health check probes adot_health_check_port path "/"
      # matcher 200 (main.tf). DOCKER-VALIDATED against
      # public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1: the collector logs
      # "Everything is ready. Begin running and processing data." and stays up, and
      # GET http://0.0.0.0:13133/ returns HTTP 200 {"status":"Server available",...}.
      health_check = {
        endpoint = "0.0.0.0:${var.adot_health_check_port}"
      }
    }

    receivers = {
      otlp = {
        protocols = {
          http = {
            endpoint              = "0.0.0.0:4318"
            max_request_body_size = var.adot_receiver_max_request_body_size
            include_metadata      = true
            # Content-type handling (D5): the OTLP/HTTP receiver natively accepts only
            # OTLP payloads (application/x-protobuf and application/json) and rejects any
            # other Content-Type by design. There is no allowed_content_types config key
            # on the OTLP receiver (ADOT v0.43.1) - setting one makes the collector fail
            # config validation and exit, so it is omitted; the allowlist is intrinsic.
          }
        }
      }
    }

    processors = {
      memory_limiter = {
        # Hard limit -- traffic is refused above this threshold (D5).
        limit_mib = var.memory_limiter_limit_mib
        # Soft spike limit -- throttling begins when memory exceeds (limit - spike) (D5).
        spike_limit_mib = var.memory_limiter_spike_limit_mib
        check_interval  = "1s"
      }
      # Sized for high-concurrency ingestion (var.adot_batch_send_batch_size /
      # var.adot_batch_timeout_seconds) so records are grouped into larger batches before
      # reaching the awscloudwatchlogs / awscloudwatchlogs/structured exporters instead of
      # relying on the OTel Collector Contrib built-in batch-processor defaults.
      batch = {
        send_batch_size = var.adot_batch_send_batch_size
        timeout         = "${var.adot_batch_timeout_seconds}s"
      }
      # Route structured-OTLP records to the raw_log=false pipeline and everything else to the
      # existing raw_log=true pipeline (input-driven, mutually exclusive). error_mode ignore = a
      # record whose service.name attribute is absent/unevaluable is KEPT (never dropped by a
      # filter error), so a body-contract record can never be lost.
      "filter/keep_structured" = {
        error_mode = "ignore"
        logs = {
          log_record = [local.structured_filter_keep_condition]
        }
      }
      "filter/drop_structured" = {
        error_mode = "ignore"
        logs = {
          log_record = [local.structured_filter_drop_condition]
        }
      }
      # Structured-OTLP METRICS routing (mirrors filter/keep_structured, but in the metrics
      # `datapoint` OTTL context). Reuses local.structured_filter_keep_condition VERBATIM: the
      # condition inspects a resource attribute (service.name), which is identical whether the
      # signal is logs or metrics, so the same input-driven, empty-list-inert expression gates
      # both pipelines (DRY -- one source of truth for "is this a structured-OTLP record").
      "filter/keep_structured_metrics" = {
        error_mode = "ignore"
        metrics = {
          datapoint = [local.structured_filter_keep_condition]
        }
      }
      # SEPARATE memory_limiter/batch instances for the metrics pipeline (ISOLATION). The
      # logs `memory_limiter` above is intentionally NOT reused: an OTLP metrics burst (e.g. a
      # spike in structured-OTLP tool sessions) must never trip the shared limiter and cause it to also
      # drop in-flight example-cli/body-contract LOG records. Each pipeline gets its own memory budget.
      "memory_limiter/metrics" = {
        limit_mib       = var.memory_limiter_limit_mib
        spike_limit_mib = var.memory_limiter_spike_limit_mib
        check_interval  = "1s"
      }
      "batch/metrics" = {}
    }

    exporters = {
      awscloudwatchlogs = {
        # Target the dedicated telemetry ingest log group (same source as the
        # aws_cloudwatch_log_group created in main.tf; DRY).
        log_group_name  = local.telemetry_log_group_name
        log_stream_name = local.telemetry_log_stream_name
        region          = local.region
        # raw_log emits only log.Body().AsString() as the event message so .tool stays
        # top-level for downstream partitioning and column mapping (MANDATORY).
        raw_log = true
        # Match the created group's retention so the exporter's PutRetentionPolicy is a
        # no-op against the Terraform-managed retention (transient hop; durable copy is
        # the Parquet lake).
        log_retention = var.telemetry_log_retention_in_days
        # Sized for high-concurrency ingestion (var.adot_exporter_sending_queue_size /
        # var.adot_exporter_sending_queue_num_consumers) so a CloudWatch Logs delivery
        # slowdown does not immediately apply backpressure to the OTLP receiver.
        sending_queue = {
          enabled       = true
          queue_size    = var.adot_exporter_sending_queue_size
          num_consumers = var.adot_exporter_sending_queue_num_consumers
        }
        retry_on_failure = {
          enabled = true
        }
      }
      # Structured exporter for structured-OTLP records: raw_log=false writes the FULL log
      # record ({body, attributes, resource, scope}) as the CWL message so the data-lake
      # cwl_split transform can read the attributes and reshape. Distinct log_stream_name (same
      # log group) so the two exporters never contend on one stream; the group-level
      # subscription filter in main.tf captures both streams into the one Firehose. Empirically
      # verified against the pinned ADOT image: raw_log=false emits the flat attributes/resource
      # maps the reshape reads.
      "awscloudwatchlogs/structured" = {
        log_group_name  = local.telemetry_log_group_name
        log_stream_name = local.structured_log_stream_name
        region          = local.region
        raw_log         = false
        log_retention   = var.telemetry_log_retention_in_days
        # Same input-driven sending_queue sizing as the raw awscloudwatchlogs exporter above
        # (DRY-in-spirit: both exporters target the same log group/Firehose hop and should
        # scale together).
        sending_queue = {
          enabled       = true
          queue_size    = var.adot_exporter_sending_queue_size
          num_consumers = var.adot_exporter_sending_queue_num_consumers
        }
        retry_on_failure = { enabled = true }
      }
      # Structured-OTLP METRICS exporter: awsemf converts OTLP metric datapoints into
      # CloudWatch EMF (Embedded Metric Format) log events, written to a THIRD stream in the
      # SAME telemetry ingest log group (local.telemetry_log_group_name) -- so the EXISTING
      # group-scoped match-all subscription filter (main.tf) forwards this stream to the
      # data-lake Firehose too, with no new group/subscription/Firehose. The keys below are
      # DOCKER-VALIDATED against public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1
      # (the collector reaches "Everything is ready. Begin running and processing data.").
      #   - resource_to_telemetry_conversion.enabled = true is MANDATORY: without it the
      #     EMF event carries no service.name field, so the data-lake reshape's service->tool
      #     mapping cannot classify the row.
      #   - dimension_rollup_option = "NoDimensionRollup" + metric_declarations with a single
      #     empty dimension set ([[]]) matching every metric name (".*") emits exactly one
      #     coarse zero-dimension CloudWatch metric per metric name -- awsemf always emits at
      #     least one CW metric per name, so this is the minimal/negligible-cost shape; the
      #     durable per-attribute data lives in the reshaped Parquet lake row, not in CW metrics.
      awsemf = {
        region          = local.region
        namespace       = var.claude_metrics_emf_namespace
        log_group_name  = local.telemetry_log_group_name
        log_stream_name = local.structured_metrics_log_stream_name
        resource_to_telemetry_conversion = {
          enabled = true
        }
        dimension_rollup_option = "NoDimensionRollup"
        metric_declarations = [
          {
            dimensions            = [[]]
            metric_name_selectors = [".*"]
          }
        ]
      }
    }

    service = {
      extensions = ["health_check"]
      pipelines = {
        # Existing raw_log=true path -- now with an inverse filter that drops structured-OTLP
        # records (they are handled by logs/structured). For every non-structured record this
        # pipeline is BYTE-UNCHANGED from before; with an empty structured_otlp_service_names the
        # filter drops nothing.
        logs = {
          receivers  = ["otlp"]
          processors = ["memory_limiter", "filter/drop_structured", "batch"]
          exporters  = ["awscloudwatchlogs"]
        }
        # Structured-OTLP path: keep only the registered structured services, export raw_log=false.
        "logs/structured" = {
          receivers  = ["otlp"]
          processors = ["memory_limiter", "filter/keep_structured", "batch"]
          exporters  = ["awscloudwatchlogs/structured"]
        }
        # Structured-OTLP METRICS path (supersedes D27.3's "no metrics pipeline"): keep only
        # structured-OTLP metric datapoints (empty structured_otlp_service_names -> the reused
        # keep condition is "true" -> every datapoint dropped -> INERT, identical contract to
        # logs/structured) and export as CloudWatch EMF via awsemf. Isolated memory_limiter/batch
        # instances (never the shared logs ones) so a metrics burst cannot starve example-cli logs.
        # Still no AMP endpoint / prometheusremotewrite exporter -- delivery is via CloudWatch
        # Logs EMF events, same transport as every other signal in this module.
        "metrics/structured" = {
          receivers  = ["otlp"]
          processors = ["memory_limiter/metrics", "filter/keep_structured_metrics", "batch/metrics"]
          exporters  = ["awsemf"]
        }
      }
    }
  })

  # ---------------------------------------------------------------------------
  # WAF managed rule groups (docs/terragrunt-concepts.md). SINGLE SOURCE OF TRUTH:
  # the waf_webacl module block in main.tf consumes local.waf_managed_rule_groups,
  # and BOTH the waf_managed_rule_names and waf_managed_rule_groups_echo outputs
  # derive from it (DRY) so the configured groups and the Terratest assertions can
  # never drift. Three AWS managed rule groups at fixed priorities p10/p20/p30, each
  # with group-level override_action = "none" (fully enforced).
  #
  # OPEN-INGESTION POSTURE: this reference is a PUBLIC telemetry collector whose whole
  # purpose is to capture usage data from ANY tool a company runs (example-cli and others) on
  # ANY network -- GitHub-hosted / self-hosted / cloud CI runners included. The
  # AWSManagedRulesAnonymousIpList group (HostingProviderIPList / AnonymousIPList) is
  # therefore DELIBERATELY OMITTED: it blocks every hosting/cloud/VPN source IP, which
  # are exactly the legitimate emitters here (a GitHub Actions runner emit returned 403
  # solely on this rule; the WAF logs show AnonymousIpList blocking clean hosting IPs
  # while the KNOWN-MALICIOUS AmazonIpReputationList rule blocked nothing legitimate).
  # Only known-malicious IPs are blocked (AmazonIpReputationList, p30). Payload attacks
  # (CommonRuleSet SQLi/XSS/NoUserAgent p10 + KnownBadInputs p20) and flooding
  # (RateLimitPerIP, primitive p100) stay fully enforced, and the pipeline's own
  # metadata-extraction routes any malformed record to errors/ (never queryable).
  #
  # BUG-1 fix (WAF blocks OTLP bodies > ~8 KiB): AWSManagedRulesCommonRuleSet's
  # SizeRestrictions_BODY sub-rule 403s any request body larger than the WAF default
  # body-inspection limit (~8 KiB), which blocks legitimate batched / high-volume /
  # large OTLP payloads even though the ADOT receiver legitimately accepts bodies up to
  # adot_receiver_max_request_body_size (4 MiB default, D5). Per AWS guidance, retarget
  # ONLY the SizeRestrictions_BODY sub-rule to "count" via rule_action_override (the new
  # waf-webacl v1.1.0 per-sub-rule capability) so oversized bodies are counted, not
  # blocked, while every OTHER CommonRuleSet rule (SQLi, XSS, LFI, ...) and every other
  # managed group stays fully ENFORCED. Request-body shaping is enforced at the ADOT
  # receiver (D5 max_request_body_size + memory_limiter), never at WAF.
  waf_managed_rule_groups = [
    {
      name            = "AWSManagedRulesCommonRuleSet"
      vendor_name     = "AWS"
      priority        = 10
      override_action = "none"
      rule_action_overrides = [
        {
          name          = "SizeRestrictions_BODY"
          action_to_use = "count"
        },
      ]
    },
    {
      name                  = "AWSManagedRulesKnownBadInputsRuleSet"
      vendor_name           = "AWS"
      priority              = 20
      override_action       = "none"
      rule_action_overrides = []
    },
    {
      name                  = "AWSManagedRulesAmazonIpReputationList"
      vendor_name           = "AWS"
      priority              = 30
      override_action       = "none"
      rule_action_overrides = []
    },
  ]

  # Comma-separated managed rule group names in priority order, derived from the single
  # source of truth above (DRY). Exposed via the waf_managed_rule_names output for the
  # Terratest docs/terragrunt-concepts.md assertions.
  waf_managed_rule_names = join(",", [for g in local.waf_managed_rule_groups : g.name])

  # ---------------------------------------------------------------------------
  # CWL-to-Firehose role trust SourceArn (BUG-2 fix: CWL->Firehose delivered 0 records).
  #
  # logs.<region>.amazonaws.com assumes aws_iam_role.cwl_to_firehose to put subscription-filter
  # records into the data-lake Firehose. CloudWatch Logs presents aws:SourceArn for this role in
  # TWO different forms depending on the phase, and the trust ArnLike condition must allow BOTH
  # (empirically verified in qa 333333333333 -- a single-form pattern fails one of the phases):
  #
  #   * CREATION  (aws logs PutSubscriptionFilter): CloudWatch Logs synchronously assumes the role
  #     and delivers a TEST message, presenting aws:SourceArn = the BARE log-group ARN with NO
  #     trailing ':*' (arn:aws:logs:<region>:<account>:log-group:/telemetry/<ns>/ingest/otlp-logs).
  #     A ':*'-only pattern does NOT match this, so PutSubscriptionFilter fails with
  #     "InvalidParameterException: Could not deliver test message ... ACTIVE state" and the apply
  #     never creates the filter.
  #   * RUNTIME   (ongoing log delivery): CloudWatch Logs assumes the role presenting
  #     aws:SourceArn = the log-group ARN WITH the trailing ':*' segment (.../otlp-logs:*, the
  #     canonical log-group ARN form DescribeLogGroups returns). A BARE-only pattern does NOT match
  #     this, so delivery is silently denied -- the filter is created but Firehose IncomingRecords
  #     stays 0 and the S3 raw/ prefix stays empty (the original BUG-2 symptom, data lake dark).
  #
  # The aws_cloudwatch_log_group.arn attribute is the BARE form (no ':*'), so the trust allows both
  # the bare ARN AND that ARN with ':*' appended. ArnLike with a list is an OR, scoped to EXACTLY
  # this one log group (least privilege). Single source of truth: the trust policy in main.tf and
  # the cwl_to_firehose_trust_source_arns_echo output both reference this local (DRY).
  cwl_to_firehose_trust_source_arns = [
    aws_cloudwatch_log_group.telemetry_ingest.arn,
    "${aws_cloudwatch_log_group.telemetry_ingest.arn}:*",
  ]

  # SSM parameter path for the ADOT config SecureString.
  adot_config_ssm_path = "/telemetry/${var.namespace}/ingest/adot-config"

  # ALB target group names are capped at 32 chars by AWS (alphanumeric + hyphens).
  # service_name is namespace-derived and can exceed 32 chars, so derive a
  # deterministic, collision-safe <=32 target group name using the repo hash-suffix
  # scheme: 20-char service_name prefix + 8-char md5 suffix + "-tg" = 31 chars.
  adot_target_group_name = "${substr(var.service_name, 0, 20)}-${substr(md5(var.service_name), 0, 8)}-tg"

  # Public and private subnet lists derived from the subnet_layout input.
  public_subnets  = [for s in var.subnet_layout : s if s.public]
  private_subnets = [for s in var.subnet_layout : s if !s.public]

  # ---------------------------------------------------------------------------
  # ALB target-group health check (single source of truth, DRY).
  # Probes the ADOT health_check extension on adot_health_check_port (13133) at path
  # "/" expecting HTTP 200 -- NOT the OTLP/HTTP receiver on adot_container_port (4318),
  # which returns 404 on "/" (DOCKER-VALIDATED) and produces Target.ResponseCodeMismatch.
  # Consumed by the adot_service ALB target group (main.tf) AND the *_echo outputs
  # (outputs.tf) so the configured values and the Terratest assertions never drift.
  # ---------------------------------------------------------------------------
  adot_target_group_health_check = {
    path                = "/"
    port                = tostring(var.adot_health_check_port)
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

# Data source for region used in locals (awscloudwatchlogs exporter region + the
# logs.<region> service principal in the CWL-to-Firehose role trust policy).
data "aws_region" "current" {}

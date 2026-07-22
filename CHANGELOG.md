# Changelog

All notable changes to this repository will be recorded in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This repository uses per-module semver tags of the form `<module_path>/v<x.y.z>`.

---

## [Unreleased]

### Added

- Initial public release of the telemetry platform: an AWS-native, input-driven
  OpenTelemetry ingestion platform (OTLP/HTTP collector on ECS Fargate behind
  CloudFront + WAF → Kinesis Firehose → S3 Parquet data lake → Glue catalog + Athena),
  deployed with Terragrunt across a multi-account topology. See `README.md` and
  `docs/deploy-from-scratch.md` to stand it up in your own AWS accounts.

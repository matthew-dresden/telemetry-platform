# 0019. Security by hardening: keep the suppression list empty

- Status: Accepted
- Era: E2E hardening (real-apply)

## Context

Static misconfiguration scanning over the Terraform and Terragrunt code surfaced genuine defects rather than noise: resources missing server access logging, missing request tracing, and missing object versioning. A failing scan admits two responses — suppress the finding, or fix the underlying configuration. Suppression hides a real weakness behind an exception entry and invites drift, because the next reviewer sees a passing scan over an unhardened resource.

A second constraint shapes how the fixes are proven. Continuous integration runs without long-lived write credentials, so an un-gated CI job cannot itself apply infrastructure against a real account to confirm that the hardened resources behave as declared. Plan-time checks alone do not exercise apply-time behavior.

## Decision

Treat every scan finding as a real defect to be fixed in code, and keep the project suppression list empty by default. Any entry added later requires documented human review, so the empty file is the visible proof that nothing was waved through.

- Harden the flagged resources directly: enable server access logging, enable X-Ray tracing where applicable, and enable object versioning.
- Route access logs through an explicit `access_log_bucket_name` input rather than letting each resource invent its own destination, so the logging target is a declared, reviewable dependency.
- Cover the apply path, not just the plan, with Terratest that provisions real resources under a run-id-scoped namespace so parallel and repeated runs never collide. Run that apply through a gated job, which is the only place that holds the write credentials the apply needs.

## Consequences

The posture is fully code-managed. Every control is declared and reviewable in the configuration, there are no hidden exceptions, and the suppression list stays empty as the conspicuous default — any future entry stands out and demands documented review. Hardening for logging, tracing, and versioning is enforced uniformly across the resources the scans flagged. Apply-time behavior is verified by run-id-scoped, gated Terratest, so the only credential-bearing path is the controlled job and write credentials are never exposed to un-gated CI. The full security posture these controls compose is described in [security.md](../security.md).

See the [ADR index](README.md).

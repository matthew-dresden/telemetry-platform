# 0020. Move per-env DNS zones, CMK, and SSM into the foundation layer

- Status: Accepted
- Era: E2E hardening (real-apply)

## Context

A destroy-and-recreate roundtrip of a service-instance set surfaced a DNS propagation window:
because the per-environment hosted zone lived alongside the destroyable service resources,
tearing the set down and rebuilding it recreated the zone and changed its name servers. New
name servers force an apex re-delegation, and the change does not take effect until DNS
caches expire, so each rebuild incurred a propagation wait before the platform resolved
again.

The same unit that owned the hosted zone also owned resources that are expensive or
disruptive to recreate: the `telemetry-config` customer-managed KMS key and the SSM seed
parameters. Recreating a customer-managed key on every roundtrip churns key material and any
references to it, compounding the cost of a routine set rebuild.

## Decision

Relocate the whole per-environment `dns-prod-zone` unit, including the hosted zone, the
`telemetry-config` customer-managed key, and the SSM seed parameters, into the long-lived
foundation tier, where it is applied once per account out of band rather than as part of the
destroyable service tree.

The DNS records that front the CloudFront-served collector endpoint stay in the
service tree. They are published per service-instance set, so they are created and destroyed
with the set they belong to, while the zone that contains them persists in the foundation
tier.

## Consequences

Destroying and recreating a service-instance set no longer recreates the hosted zone or the
customer-managed key. The zone's name servers stay fixed across roundtrips, so no apex
re-delegation and no DNS propagation wait is incurred on a rebuild, and the `telemetry-config`
key persists rather than being churned. The cost is that the DNS foundation now belongs to
the operator-ordered bootstrap tier and must be applied before the dependency-ordered
singletons and service-instance sets that consume it. The resulting bring-up order is
documented in [bootstrap ordering](../bootstrap-ordering.md).

See the [ADR index](README.md).

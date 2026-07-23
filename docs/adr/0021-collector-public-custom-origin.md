# 0021. CloudFront public custom origin pointed at the collector FQDN

- Status: Superseded by ADR-internal-alb-cloudfront-vpc-origin
- Era: E2E hardening

## Context

The collector serving path places a CloudFront distribution in front of the
collector's load balancer, and the public collector FQDN is a Route 53 alias
record that resolves to that same distribution. The distribution needs an origin
that tells CloudFront where to forward each ingest request. The origin had to be
expressed in a way that also worked with a load balancer that is not
internet-facing.

## Decision

Configure the distribution with a public custom origin addressed by public DNS
name, using the collector FQDN as the origin domain.

## Consequences

Because the collector FQDN aliases back to the same distribution, CloudFront
resolved its own origin to itself, producing a CloudFront-to-CloudFront
self-loop that returned HTTP 403 on every request and left public ingest
non-functional. A public custom origin addressed by public DNS name also cannot
reach an internal load balancer, since public resolution never returns the
load balancer's private addresses. The approach was therefore replaced.

## Superseded by

This decision was superseded by
[the internal ALB reached through a CloudFront VPC origin](0022-internal-alb-cloudfront-vpc-origin.md).
CloudFront now reaches the internal load balancer privately by resource
reference rather than by resolving a public origin domain, which removes the
self-loop and keeps the load balancer internal.

See the [ADR index](README.md).

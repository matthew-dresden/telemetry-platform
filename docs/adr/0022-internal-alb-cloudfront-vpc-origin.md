# 0022. Internal ALB behind CloudFront over a VPC origin

- Status: Accepted
- Era: E2E hardening

## Context

The collector ingest path puts CloudFront in front of an Application Load Balancer that fronts the ADOT collector. The CloudFront distribution originally used a public "custom" origin whose `domain_name` was the collector service FQDN. That FQDN is a Route53 A-alias pointing back to the same distribution, so CloudFront resolved its own domain and looped back to itself instead of reaching the load balancer, returning an HTTP 403.

A public custom origin also cannot reach an internal (`scheme = internal`) ALB at all, since a public origin must resolve to a publicly routable target. The collector therefore needed an origin mechanism that both avoids the self-loop and can reach a load balancer that has no public listener.

## Decision

Configure the collector CloudFront distribution with a VPC origin: `origin_type = "vpc"` and `vpc_origin_arn` set to the ALB ARN. CloudFront routes to the ALB by its ARN through the VPC origin rather than by resolving public DNS, which removes the self-loop and lets it reach the internal ALB.

- The ALB stays internal; CloudFront is the single public entry point.
- The ALB HTTPS-listener ingress is tightened to the CloudFront origin-facing managed prefix list, supplied as a module input (`var.cloudfront_origin_facing_prefix_list_id`) rather than a plan-time data lookup, so the module keeps zero plan-time AWS reads.
- `domain_name` remains the collector service FQDN, used only for the origin Host header and TLS SNI so the origin certificate validates over the `https-only` origin handshake.
- The WAF `SizeRestrictions_BODY` sub-rule is retargeted to `count` so legitimate large OTLP bodies are not blocked; request body-size shaping is applied at the ADOT receiver instead of at the WAF edge.

## Consequences

The origin self-loop is gone, the ALB is internal and reachable only through the CloudFront VPC origin, and OTLP ingest returns HTTP 200 end to end. Keeping the origin-facing prefix list as an input preserves the module's zero plan-time AWS reads, so cross-account plans continue to succeed. CloudFront remains the only public entry point, and the viewer-facing routing (the pretty A-alias to CloudFront) is unchanged because only the origin side of the distribution changed.

## Supersedes

This decision supersedes the [collector public custom origin](0021-collector-public-custom-origin.md) approach, in which the distribution used a public custom origin pointed at the collector service FQDN. That approach self-looped and could not reach the internal ALB; it is no longer used.

See the [ADR index](README.md).

# Security policy

What this is: how to report a security vulnerability in telemetry-platform and what to
expect in response. Read this before disclosing anything; for the controls that protect
the platform, see [the security posture](../docs/security.md).

## Reporting a vulnerability

- Do not open a public GitHub issue, pull request, or discussion for a security report —
  that discloses the issue before it can be fixed.
- Report privately through GitHub private vulnerability reporting: open the repository's
  **Security** tab and choose **Report a vulnerability** (GitHub Security Advisories).
- If you cannot use private reporting, email the maintainers at security@example.com and ask
  for a private channel before sharing any details.

Please include enough to reproduce and assess the issue:

- The affected component or endpoint (collector edge, pipeline, IaC, CI/CD).
- The commit SHA or release tag you observed it on.
- Reproduction steps, the impact, and any supporting logs — with all credentials, tokens,
  and personal data redacted.

## Scope

In scope:

- The collector ingest edge and the data pipeline in this repository.
- The infrastructure-as-code and the CI/CD supply chain defined here.

Out of scope:

- Vulnerabilities in third-party AWS services themselves (report those to AWS).
- Findings that require already-compromised credentials or privileged access to reproduce.

## What to expect

- Acknowledgement target: within three business days of a private report.
- We triage and validate the report, then remediate through the standard delivery process
  (branch, pull request, required checks, release) described in
  [the contributing guide](../docs/contributing.md).
- We coordinate disclosure timing with the reporter and credit reporters who want it.

## Handling sensitive data in reports

Never include credentials, access tokens, encryption keys, or customer personal data in a
report; redact them first. The platform's data-handling and encryption controls are
described in [the security posture](../docs/security.md).

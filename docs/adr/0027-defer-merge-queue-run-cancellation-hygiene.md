# 0027. Defer GitHub Merge Queue; rely on run-cancellation hygiene

- Status: Superseded by ADR-0028
- Era: Go-live polish

## Context

Ordering of writes to `main` can be enforced either by GitHub's native Merge
Queue, which serializes ready pull requests into a strict FIFO and validates
each on a speculative ref, or by concurrency hygiene on the workflows that run
after a merge. The two are not interchangeable for this repository, because the
release does not land through a pull request: the platform release bot commits
the version bump and pushes the `chore(release)` commit and its SemVer tag
directly to `main` using its installation token.

A merge queue treats every write to `main` as a queued entry. With the queue
enforced and no bypass for the release bot, the bot's direct push has no pull
request to queue, so the queue rejects it and the release fails. Enabling the
queue therefore required first establishing and verifying a bypass actor for the
release bot's direct push — a guarantee not yet proven at this point in go-live.

Independently of the queue, the release lane and the prod-apply lane must never
have an in-flight run cancelled by a newer one. Cancelling a running release
would abandon a half-published tag and a held branch lock; cancelling a running
apply would corrupt shared remote state. That requirement holds whether or not a
merge queue is in force.

## Decision

Defer enabling the GitHub Merge Queue. Until a release-bot bypass is established
and verified, govern serialization through CI hygiene on the post-merge lanes
rather than through a queue.

Each global lane runs in a repo-wide concurrency group with
`cancel-in-progress: false`, so a running release or apply is protected from
cancellation by a later push. Per-pull-request lanes keep
`cancel-in-progress: true`, where superseding a stale run is legitimate, and no
lane shares a concurrency group with another lane, so a pull-request re-push can
never cancel a release or a prod apply.

## Consequences

Releases are not broken by premature queue enforcement: the release bot keeps
pushing its commit and tag directly to `main`, and the post-merge lanes
serialize themselves through their concurrency groups instead of through a
queue. Ordering is provided by hygiene rather than by the platform, so the
arrival rate of merges is not throttled and the guarantee depends on every lane
carrying the correct concurrency configuration rather than on a single enforced
ruleset.

Once a bypass actor for the release bot's direct push was established and
verified, the queue could be enabled without breaking releases, and the FIFO
ordering moved into the platform. That successor decision supersedes this one.

## Superseded by

Superseded by
[0028. FIFO merge queue with serialized release lanes](0028-fifo-merge-queue-release-lanes.md).

See the [ADR index](README.md).

# 0028. FIFO-serialized release/apply lanes plus GitHub Merge Queue and branch protection

- Status: Accepted
- Era: Go-live polish

## Context

Two workflows mutate shared global state when a change lands on `main`. The
release lane (`main-validation.yml`) commits a version bump, tags a SemVer
release, and pushes to the single `main` ref under one global branch lock. The
prod-apply lane (`terragrunt-apply.yml`) applies Terragrunt changes against
shared remote state. Both must run to completion in order: two releases at once
race on the branch lock, the lock marker variable, and `git push origin main`,
and two concurrent applies corrupt remote state.

Each lane is one repo-wide concurrency group. Under GitHub's default queue mode
(`queue: single`) at most one run may be pending per group, and a newer queued
run supersedes the pending one, so a burst of near-simultaneous merges can drop
a middle release or apply run and lose a module release.

Adopting GitHub's Merge Queue to throttle arrivals was previously deferred for a
concrete reason: the release App must push its `chore(release)` commit and tag
directly to `main` with no pull request, and a naive merge-queue rule rejects
that direct push, breaking every release. The queue only became safe to adopt
once a bypass actor for the release App was verified to allow that direct push
while still forcing every human change through a gated PR.

## Decision

Treat both global lanes as serialized FIFO queues with `cancel-in-progress:
false`: never cancel an in-flight release or prod apply. A run is cancelled only
when genuinely superseded by a newer push to the same PR; merging at the same
time as another contributor never cancels a lane.

```yaml
concurrency:
  group: release-pipeline   # or tg-apply-prod for the prod-apply lane
  cancel-in-progress: false
```

Land every human change through the GitHub Merge Queue, governed by the
`main-protection` repository ruleset on `refs/heads/main`: `merge_method:
SQUASH`, `grouping_strategy: ALLGREEN`, and exactly one PR per push to `main` so
the per-PR release model holds. Register the release App as an `Integration`
bypass actor (and org admins as a second bypass actor) so the bot's direct
`chore(release)` push and tag, and an urgent admin fix, are allowed while no
other path can write `main` without a PR. The release App bypass is the single
load-bearing entry: without it every release would fail.

Make `merge_group` fail-closed. Both required aggregates trigger on the
speculative merge-group ref, and the scope-override authorization resolves
fail-closed against org membership on that ref. The branch protection on `main`
combines a minimal classic rule whose only role is the release lock with the
ruleset's required status checks (`required-checks` from `pr-validation.yml` and
`codeql-required`, the single CodeQL aggregate from `codeql.yml`), a required
pull request with zero approvals, and the non-fast-forward and deletion guards.
A required check that never ran on the speculative ref hangs the queue rather
than letting an ungated change through. The release publishes its version-bump
commit and SemVer tag together to `main` under the global branch lock, so the
tag is pushed atomically with the release commit on the serialized lane.

## Consequences

The lanes no longer cancel an in-flight release or apply, and because the Merge
Queue lands exactly one PR per push to `main` it throttles the arrival rate, so
the lanes stop stacking pending runs into a dropped release or apply. The
version-bump commit and its SemVer tag are pushed together under the global
lock, so a tag is not lost to a concurrent push. Every human change is gated: a
PR is required, both `required-checks` and `codeql-required` must be green on the
speculative ref before the queue merges, and Advanced Security code scanning must
stay enabled while `codeql-required` is required.

One residual remains. The fully declarative hardening, `queue: max` for a deep
FIFO of pending runs, is deferred because the pinned `actionlint` does not yet
recognize the `queue` concurrency key and the project never bypasses a linter
finding. Until that toolchain bump lands, the Merge Queue throttling the arrival
rate is what keeps runs from stacking.

## Supersedes

This decision supersedes
[deferring the merge queue and relying on run-cancellation hygiene](0027-defer-merge-queue-run-cancellation-hygiene.md).
Run-cancellation hygiene alone left the default single-pending-run queue able to
drop a release under a burst; the Merge Queue plus the verified release-App
bypass closes that gap while gating every PR.

See the [ADR index](README.md).

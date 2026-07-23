"""check_release_commit -- detect release commits to prevent re-triggering.

should_skip=true iff the commit message starts with the RELEASE_COMMIT_PREFIX
AND the actor is the release bot (BOT_ACTOR).

B16 hardening: a human-authored chore(release): commit must NOT skip, so a
contributor cannot forge a release-skip by crafting a commit message. Both
conditions must be true simultaneously.

Usage (invoked by `make check-release-commit`):
    uv run python -m scripts.check_release_commit \\
        --commit-message "chore(release): kms-key v0.1.0" \\
        --actor "github-actions[bot]" \\
        --output "$GITHUB_OUTPUT"

Exit codes:
    0 -- always (the skip flag is written to --output; failure is not an error)
    1 -- argument or I/O error
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from scripts.constants import (
    BOT_ACTOR,
    GH_ERROR_PREFIX,
    OUTPUT_KEY_SHOULD_SKIP,
    RELEASE_COMMIT_PREFIX,
    write_output,
)


@dataclass(frozen=True)
class ReleaseCommitResult:
    """Result of the release-commit check.

    Attributes:
        should_skip: True iff the commit is a bot-authored release commit.
    """

    should_skip: bool


def check_release_commit(commit_message: str, actor: str) -> ReleaseCommitResult:
    """Determine whether the workflow should skip because this is a release commit.

    should_skip=True ONLY when ALL of:
    - commit_message starts with RELEASE_COMMIT_PREFIX ('chore(release):')
    - actor equals BOT_ACTOR ('github-actions[bot]')

    A human-authored chore(release): does NOT skip (B16 bypass close).

    Args:
        commit_message: The commit message to inspect.
        actor: The GitHub actor (committer/pusher) identity.

    Returns:
        ReleaseCommitResult with should_skip set appropriately.
    """
    is_release_message = commit_message.startswith(RELEASE_COMMIT_PREFIX)
    is_bot_actor = actor == BOT_ACTOR
    should_skip = is_release_message and is_bot_actor
    return ReleaseCommitResult(should_skip=should_skip)


def main() -> None:
    """CLI entry point for check_release_commit.

    Parses arguments, runs the check, and writes should_skip to --output.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check whether a commit is a bot release commit that should skip "
            "the release pipeline (prevents re-trigger loops)."
        )
    )
    parser.add_argument(
        "--commit-message",
        required=True,
        help="The commit message to inspect.",
    )
    parser.add_argument(
        "--actor",
        required=True,
        help="The GitHub actor (committer identity).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the GITHUB_OUTPUT file to append should_skip=true/false.",
    )
    args = parser.parse_args()

    try:
        result = check_release_commit(
            commit_message=args.commit_message,
            actor=args.actor,
        )
    except Exception as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    skip_value = "true" if result.should_skip else "false"
    try:
        write_output(args.output, OUTPUT_KEY_SHOULD_SKIP, skip_value)
    except OSError as exc:
        print(
            f"{GH_ERROR_PREFIX}ERROR: Failed to write to output file {args.output!r}: {exc}\n"
            "Ensure the output file path is writable.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"{OUTPUT_KEY_SHOULD_SKIP}={skip_value}")


if __name__ == "__main__":
    main()

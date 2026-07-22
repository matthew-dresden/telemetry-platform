"""release_steps -- git fetch/reset/identity/publish subcommands under uv.

Keeps all git plumbing in Python (under make, ledger D11) rather than raw
workflow shell. Each subcommand maps to a `make` target:

    make git-fetch REMOTE=origin BRANCH=main
        -> release_steps fetch --remote origin --branch main

    make git-reset-hard REF=origin/main
        -> release_steps reset --ref origin/main

    make git-identity USER=... EMAIL=...
        -> release_steps identity --user ... --email ...

    make publish-release TAG_PREFIX=... VERSION=... FULL_TAG=... CHANGELOG_PATH=...
        -> release_steps publish --tag-prefix ... --version ... --full-tag ...
                                 --changelog-path ... [--remote origin] [--branch main]

No time.sleep() or time-based synchronization anywhere (ledger mandatory standard).
All failures exit non-zero with a clear error message.

Exit codes:
    0 -- subcommand succeeded
    1 -- error; details on stderr
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.constants import GH_ERROR_PREFIX


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command, failing fast on non-zero exit.

    Args:
        cmd: Command and arguments as a list.
        check: If True, raise RuntimeError on non-zero exit. Defaults to True.

    Returns:
        The CompletedProcess result.

    Raises:
        RuntimeError: If check=True and the command exits non-zero.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"ERROR: Command {cmd!r} exited with code {result.returncode}.\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def run_fetch(remote: str, branch: str) -> None:
    """Fetch the specified branch from the specified remote.

    Args:
        remote: The git remote name (e.g. 'origin').
        branch: The branch to fetch (e.g. 'main').

    Raises:
        RuntimeError: If git fetch fails.
    """
    _run(["git", "fetch", remote, branch])


def run_reset(ref: str) -> None:
    """Hard-reset the working tree to the specified ref.

    Args:
        ref: The git ref to reset to (e.g. 'origin/main').

    Raises:
        RuntimeError: If git reset fails.
    """
    _run(["git", "reset", "--hard", ref])


def run_identity(user: str, email: str) -> None:
    """Configure the git user identity for the release commit.

    Args:
        user: The git user.name to set (e.g. 'github-actions[bot]').
        email: The git user.email to set.

    Raises:
        RuntimeError: If any git config command fails.
    """
    _run(["git", "config", "user.name", user])
    _run(["git", "config", "user.email", email])


def run_publish(
    tag_prefix: str,
    version: str,
    full_tag: str,
    changelog_path: str,
    remote: str = "origin",
    branch: str = "main",
) -> None:
    """Commit version files, create the tag, push atomically, and create a GitHub release.

    Sequence (must be atomic):
    1. git add (stage all tracked modified files)
    2. git commit -m "chore(release): <full_tag>"
    3. git tag <full_tag>
    4. git push --atomic <remote> <branch> <full_tag>  (all-or-nothing: branch + tag together)
    5. gh release create <full_tag> --notes-file <changelog_path>

    No time.sleep() between any step.

    Args:
        tag_prefix: The tag prefix (e.g. 'providers/aws/primitives/kms-key/v').
        version: The new version string (e.g. '0.1.0').
        full_tag: The complete tag (e.g. 'providers/aws/primitives/kms-key/v0.1.0').
        changelog_path: Path to the changelog file for the GitHub release notes.
        remote: The git remote name to push to. Defaults to 'origin'.
        branch: The branch name to push. Defaults to 'main'.

    Raises:
        RuntimeError: If any git or gh command fails.
        FileNotFoundError: If changelog_path does not exist.
    """
    changelog = Path(changelog_path)
    if not changelog.exists():
        raise FileNotFoundError(
            f"ERROR: Changelog file not found: {changelog_path}\n"
            "Ensure the generate-changelog step ran successfully before publish."
        )

    commit_message = f"chore(release): {full_tag}"

    # 1. Stage all tracked modified files.
    _run(["git", "add", "-u"])
    # 2. Commit. Use --allow-empty so the release commit is created even when the
    #    VERSION file already holds the computed next version (the version_only
    #    release path: a module-scope PR that bumped only the VERSION file). In that
    #    case update_version_files is a no-op (the value is unchanged) and the
    #    transient per-module changelog is untracked, so `git add -u` stages nothing;
    #    without --allow-empty `git commit` would fail with "nothing to commit",
    #    aborting the release before the tag is cut. The empty commit is harmless: its
    #    tree is identical to the merge commit it sits on, so the tag still marks the
    #    exact released content. Code-change releases stage a real VERSION bump and
    #    commit a non-empty change exactly as before.
    _run(["git", "commit", "--allow-empty", "-m", commit_message])
    # 3. Create the annotated tag.
    _run(["git", "tag", full_tag])
    # 4. Atomic push: push the branch AND the new tag in ONE all-or-nothing
    #    transaction. `--atomic` is required (not merely listing both refs): when a
    #    branch-protection ruleset rejects the branch ref (e.g. GH013 if the release
    #    bot is ever dropped from the bypass list), a non-atomic push would still
    #    update the tag ref, leaving a dangling tag that points at an unpublished
    #    commit and poisons the next release's version derivation. `--atomic` makes
    #    the server reject BOTH refs together, so a rejected branch push can never
    #    leave an orphaned tag behind.
    _run(["git", "push", "--atomic", remote, branch, full_tag])
    # 5. Create the GitHub release.
    _run(
        [
            "gh",
            "release",
            "create",
            full_tag,
            "--notes-file",
            changelog_path,
            "--title",
            full_tag,
        ]
    )


def main() -> None:
    """CLI entry point for release_steps. Dispatches to the correct subcommand."""
    parser = argparse.ArgumentParser(
        description="Release step subcommands: fetch, reset, identity, publish."
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # fetch subcommand
    fetch_parser = subparsers.add_parser("fetch", help="git fetch <remote> <branch>")
    fetch_parser.add_argument("--remote", required=True, help="Git remote name.")
    fetch_parser.add_argument("--branch", required=True, help="Branch to fetch.")

    # reset subcommand
    reset_parser = subparsers.add_parser("reset", help="git reset --hard <ref>")
    reset_parser.add_argument("--ref", required=True, help="Git ref to reset to.")

    # identity subcommand
    identity_parser = subparsers.add_parser("identity", help="Set git user name and email.")
    identity_parser.add_argument("--user", required=True, help="git user.name value.")
    identity_parser.add_argument("--email", required=True, help="git user.email value.")

    # publish subcommand
    publish_parser = subparsers.add_parser(
        "publish",
        help="Commit, tag, push atomically, and create a GitHub release.",
    )
    publish_parser.add_argument("--tag-prefix", required=True, help="The tag prefix.")
    publish_parser.add_argument("--version", required=True, help="The new version string.")
    publish_parser.add_argument("--full-tag", required=True, help="The complete git tag.")
    publish_parser.add_argument(
        "--changelog-path", required=True, help="Path to the changelog file."
    )
    publish_parser.add_argument(
        "--remote", default="origin", help="Git remote to push to (default: origin)."
    )
    publish_parser.add_argument(
        "--branch", default="main", help="Branch name to push (default: main)."
    )

    args = parser.parse_args()

    try:
        if args.subcommand == "fetch":
            run_fetch(remote=args.remote, branch=args.branch)
        elif args.subcommand == "reset":
            run_reset(ref=args.ref)
        elif args.subcommand == "identity":
            run_identity(user=args.user, email=args.email)
        elif args.subcommand == "publish":
            run_publish(
                tag_prefix=args.tag_prefix,
                version=args.version,
                full_tag=args.full_tag,
                changelog_path=args.changelog_path,
                remote=args.remote,
                branch=args.branch,
            )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

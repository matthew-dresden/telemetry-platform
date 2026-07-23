"""calculate_version -- pure version derivation from git tags.

Globs <prefix>[0-9]* tags, strips the leading prefix (which ends with '/v'),
and derives the next <module_path>/v<x.y.z> tag per the terraform-modules scheme
(release.yml:104,114,125).

D17 anti-forgery helpers: validate_bump_agreement and derive_bump_from_api_result
enforce that the bump type comes from the GitHub API PR title, not the squash subject.

This module contains only pure library functions. The CI entrypoint that wires
environment inputs is ci_calculate_version.py.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from scripts.constants import SCOPE_CONFIG, SCOPE_MODULE
from scripts.validate_pr_title import BumpType


@dataclass(frozen=True)
class VersionResult:
    """Result of a version derivation.

    Attributes:
        next_version: The derived semver string (e.g. '0.1.0').
        full_tag: The complete git tag (e.g. 'providers/aws/primitives/kms-key/v0.1.0').
        tag_prefix: The tag prefix used (e.g. 'providers/aws/primitives/kms-key/v').
        bump: The bump type applied.
        is_initial: True when no existing tags existed (first release from 0.0.0).
    """

    next_version: str
    full_tag: str
    tag_prefix: str
    bump: BumpType
    is_initial: bool


def build_tag_prefix(scope: str, module_path: str, config_tag_prefix: str) -> str:
    """Build the tag prefix for the given scope and module path.

    For scope=module: <module_path>/v
    For scope=config: <config_tag_prefix>/v  (e.g. 'monorepo-config/v')

    Mirrors the terraform-modules scheme (release.yml:104):
        PREFIX="${MODULE}/v"

    Args:
        scope: 'module' or 'config'. Other values raise ValueError.
        module_path: The module directory path (e.g. 'providers/aws/primitives/kms-key').
            Only used when scope='module'.
        config_tag_prefix: The config tag prefix from monorepo-config.json
            (e.g. 'monorepo-config').

    Returns:
        The tag prefix string ending with '/v'.

    Raises:
        ValueError: If scope is not 'module' or 'config'.
    """
    if scope == SCOPE_MODULE:
        if not module_path:
            raise ValueError(
                "ERROR: module_path must be non-empty when scope='module'.\n"
                "Ensure the scope-detection step emits a non-empty module_path."
            )
        return f"{module_path}/v"
    if scope == SCOPE_CONFIG:
        if not config_tag_prefix:
            raise ValueError(
                "ERROR: config_tag_prefix must be non-empty when scope='config'.\n"
                "Ensure monorepo-config.json contains a 'config_tag_prefix' key."
            )
        return f"{config_tag_prefix}/v"
    raise ValueError(
        f"ERROR: Unsupported scope {scope!r} for tag prefix derivation.\n"
        "Supported scopes: 'module', 'config'. "
        "scope='terragrunt' produces no module tag."
    )


def find_latest_version(tags: list[str], tag_prefix: str) -> str | None:
    """Find the latest semver among tags matching the given prefix.

    Implements the terraform-modules glob pattern: f"{tag_prefix}[0-9]*"
    Strips exactly the tag_prefix (which ends with '/v') to get the bare semver.

    Args:
        tags: All git tags available in the repository.
        tag_prefix: The prefix to filter and strip (e.g. 'providers/aws/primitives/kms-key/v').

    Returns:
        The latest semver string (e.g. '1.2.3') or None if no matching tags exist.
    """
    glob_pattern = f"{tag_prefix}[0-9]*"
    matching = [t for t in tags if fnmatch.fnmatch(t, glob_pattern)]

    if not matching:
        return None

    # Strip exactly the tag_prefix to get the semver string.
    # tag_prefix ends with '/v', so we strip it -- not tag_prefix + "/" (B26 fix).
    versions: list[tuple[int, int, int]] = []
    valid_tags: list[str] = []
    for tag in matching:
        semver_str = tag[len(tag_prefix) :]
        parsed = _parse_semver(semver_str)
        if parsed is not None:
            versions.append(parsed)
            valid_tags.append(tag)

    if not versions:
        return None

    latest_tuple = max(versions)
    return f"{latest_tuple[0]}.{latest_tuple[1]}.{latest_tuple[2]}"


def _parse_semver(semver_str: str) -> tuple[int, int, int] | None:
    """Parse a semver string into a (major, minor, patch) tuple.

    Returns None if the string is not a valid semver.
    """
    parts = semver_str.split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        return (major, minor, patch)
    except ValueError:
        return None


def _apply_bump(major: int, minor: int, patch: int, bump: BumpType) -> tuple[int, int, int]:
    """Apply a semver bump and return the new (major, minor, patch).

    Args:
        major: Current major version.
        minor: Current minor version.
        patch: Current patch version.
        bump: The bump type to apply.

    Returns:
        New (major, minor, patch) tuple.
    """
    if bump == BumpType.MAJOR:
        return (major + 1, 0, 0)
    if bump == BumpType.MINOR:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def calculate_next_version(
    existing_tags: list[str],
    tag_prefix: str,
    bump: BumpType,
) -> VersionResult:
    """Derive the next version from existing tags and the bump type.

    Args:
        existing_tags: All git tags available (typically from `git tag -l`).
        tag_prefix: The prefix for this module/config (e.g. 'providers/aws/primitives/kms-key/v').
        bump: The semver bump type (MAJOR, MINOR, or PATCH).

    Returns:
        VersionResult with next_version, full_tag, tag_prefix, bump, is_initial.

    Raises:
        ValueError: If the computed full_tag already exists in existing_tags
            (version collision -- fail fast).
    """
    latest = find_latest_version(tags=existing_tags, tag_prefix=tag_prefix)
    is_initial = latest is None

    if is_initial:
        current_major, current_minor, current_patch = 0, 0, 0
    else:
        assert latest is not None
        parsed = _parse_semver(latest)
        if parsed is None:
            raise ValueError(
                f"ERROR: Could not parse latest semver {latest!r} for prefix {tag_prefix!r}.\n"
                "Ensure existing tags follow the <x.y.z> semver format."
            )
        current_major, current_minor, current_patch = parsed

    next_major, next_minor, next_patch = _apply_bump(
        current_major, current_minor, current_patch, bump
    )
    next_version = f"{next_major}.{next_minor}.{next_patch}"
    full_tag = f"{tag_prefix}{next_version}"

    # Fail fast if the computed tag already exists (version collision).
    if full_tag in existing_tags:
        raise ValueError(
            f"ERROR: Computed tag {full_tag!r} already exists in the repository.\n"
            f"Version {next_version} has already been released for prefix {tag_prefix!r}.\n"
            "Ensure the bump type derives a genuinely new version."
        )

    return VersionResult(
        next_version=next_version,
        full_tag=full_tag,
        tag_prefix=tag_prefix,
        bump=bump,
        is_initial=is_initial,
    )


def validate_bump_agreement(api_bump: BumpType, squash_bump: BumpType) -> None:
    """Validate that the API PR-title bump agrees with the squash-subject bump.

    D17 anti-forgery enforcement: if a merger edited the squash subject to a
    different bump type than the validated PR title, this raises ValueError.

    Args:
        api_bump: The bump type derived from the GitHub API PR title.
        squash_bump: The bump type derived from the squash-commit subject line.

    Raises:
        ValueError: If api_bump != squash_bump (forgery detected).
    """
    if api_bump != squash_bump:
        raise ValueError(
            f"ERROR: Bump type mismatch between the API PR title and the squash subject.\n"
            f"API PR title bump: {api_bump.value!r}\n"
            f"Squash subject bump: {squash_bump.value!r}\n"
            "D17: the bump type is derived from the API-fetched PR title only. "
            "A hand-edited squash subject that disagrees with the PR title is rejected.\n"
            "Fix: update the squash subject to match the PR title's commit type, "
            "or update the PR title before merging."
        )


def derive_bump_from_api_result(api_pr_title: str | None) -> BumpType:
    """Derive the bump type from the GitHub API PR title result.

    D17: this is the enforcement point. If the API returns no PR, fail fast.

    Args:
        api_pr_title: The PR title fetched from the GitHub API, or None if no PR
            was found for the commit SHA.

    Returns:
        The BumpType derived from the API PR title.

    Raises:
        ValueError: If api_pr_title is None (no PR associated with the commit).
        ValueError: If the PR title is not a valid conventional-commit title.
    """
    if api_pr_title is None:
        raise ValueError(
            "ERROR: No pull request found associated with this commit via the GitHub API.\n"
            "D17: the bump type is derived from the API-fetched PR title. "
            "A commit with no associated PR cannot have its bump type validated.\n"
            "Ensure the commit was merged via a pull request."
        )

    from scripts.validate_pr_title import parse_pr_title

    parsed = parse_pr_title(api_pr_title)
    return parsed.bump

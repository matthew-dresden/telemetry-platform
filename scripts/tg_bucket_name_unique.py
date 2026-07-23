"""tg_bucket_name_unique -- confirms derived state bucket names are unique and at or under 63 chars.

Run via: uv run python -m scripts.tg_bucket_name_unique

Scans all leaf terragrunt.hcl files under the Terragrunt live tree, derives each
unit's state bucket name using the same logic as the root terragrunt.hcl (B21),
and confirms that:
  1. No two units resolve to the same bucket name (collision detection).
  2. Every bucket name is at or under 63 characters (S3 limit).

Fails closed (D16): any collision or over-length name causes a non-zero exit.

Environment variable consumed (optional):
    TG_LIVE_ROOT    -- path to the Terragrunt live tree root (default: terragrunt/).
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BUCKET_NAME_LENGTH = 63

# Prefix length for the account-id portion in shortened names (12 digits).
ACCOUNT_ID_LENGTH = 12

# Number of chars taken from the namespace prefix in the shortened scheme (B21).
NAMESPACE_PREFIX_LENGTH = 28

# Number of chars taken from the md5 hash in the shortened scheme (B21).
HASH_SUFFIX_LENGTH = 8

# Regex to extract the locals block for bucket_name_raw derivation from account.hcl.
# In the live tree, account.hcl carries the aws_account_id as basename-derived.
ACCOUNT_ID_RE = re.compile(r'aws_account_id\s*=\s*"(\d{12})"')

# Regex to extract the final_bucket_name local if the leaf overrides it directly.
# In practice, bucket names are derived from the root's namespace formula.
BUCKET_NAME_RE = re.compile(r'final_bucket_name\s*=\s*"([^"]+)"')


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class BucketNameCollisionError(RuntimeError):
    """Raised when two units derive the same state bucket name."""


class BucketNameTooLongError(RuntimeError):
    """Raised when a derived state bucket name exceeds 63 characters."""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def shorten_bucket_name(account_id: str, namespace: str, raw_name: str) -> str:
    """Apply the B21 hash-suffix shortening scheme to produce a name at or under 63 chars.

    When the raw_name is within the limit, it is returned unchanged.
    When it exceeds the limit, the name is shortened to:
        <account_id>-<namespace[:28]>-<md5(namespace)[:8]>-tfstate

    Args:
        account_id: The 12-digit AWS account id (kept in full).
        namespace: The full namespace string (used for md5 and prefix).
        raw_name: The raw bucket name before shortening.

    Returns:
        A bucket name guaranteed to be at or under MAX_BUCKET_NAME_LENGTH chars.
    """
    if len(raw_name) <= MAX_BUCKET_NAME_LENGTH:
        return raw_name

    ns_clean = namespace.replace("_", "-")
    ns_prefix = ns_clean[:NAMESPACE_PREFIX_LENGTH]
    ns_hash = hashlib.md5(namespace.encode(), usedforsecurity=False).hexdigest()[
        :HASH_SUFFIX_LENGTH
    ]
    shortened = f"{account_id}-{ns_prefix}-{ns_hash}-tfstate"
    return shortened.lower()


def assert_bucket_names_unique(bucket_names: list[str]) -> None:
    """Assert that all bucket names are unique and within the 63-char S3 limit.

    Args:
        bucket_names: List of derived state bucket names.

    Raises:
        BucketNameTooLongError: If any name exceeds 63 characters.
        BucketNameCollisionError: If any two names are identical.
    """
    # Check length first -- a too-long name is a derivation error.
    for name in bucket_names:
        if len(name) > MAX_BUCKET_NAME_LENGTH:
            raise BucketNameTooLongError(
                f"ERROR: Derived state bucket name exceeds the S3 {MAX_BUCKET_NAME_LENGTH}-char "
                f"limit ({len(name)} chars): '{name}'\n"
                f"  The bucket name shortening (B21) must produce a name within the limit.\n"
                f"  Remedy: verify the namespace derivation in the root terragrunt.hcl and\n"
                f"  ensure shorten_bucket_name is applied before use."
            )

    seen: dict[str, int] = {}
    for i, name in enumerate(bucket_names):
        if name in seen:
            raise BucketNameCollisionError(
                f"ERROR: State bucket name collision detected: '{name}'\n"
                f"  Two units (indices {seen[name]} and {i}) derive the same bucket name.\n"
                f"  The B21 hash-suffix scheme must be applied to ensure uniqueness.\n"
                f"  Remedy: verify the namespace derivation ensures distinct names for each unit."
            )
        seen[name] = i


def _find_account_id_in_ancestors(hcl_file: pathlib.Path) -> str | None:
    """Walk up from hcl_file to find the aws_account_id in an account.hcl ancestor.

    Returns the 12-digit account id string, or None if not found.
    """
    candidate = hcl_file.parent
    while True:
        account_hcl = candidate / "account.hcl"
        if account_hcl.exists():
            content = account_hcl.read_text(encoding="utf-8")
            match = ACCOUNT_ID_RE.search(content)
            if match:
                return match.group(1)
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent


def _derive_namespace_from_path(hcl_file: pathlib.Path, terragrunt_root: pathlib.Path) -> str:
    """Derive the namespace from the unit directory path structure.

    The namespace is derived from the path components below the live/ directory:
    product/region/account/environment/env_instance/service/svc_instance

    This mirrors the root terragrunt.hcl locals derivation.
    """
    rel = hcl_file.parent.relative_to(terragrunt_root)
    parts = list(rel.parts)
    # Remove the leading "live" dir if present.
    if parts and parts[0] == "live":
        parts = parts[1:]
    return "-".join(parts).replace("_", "-")


def collect_bucket_names(
    terragrunt_root: pathlib.Path,
) -> list[tuple[pathlib.Path, str]]:
    """Collect derived state bucket names for all leaf units.

    Uses the same shortening scheme as the root terragrunt.hcl (B21).

    Args:
        terragrunt_root: Root of the Terragrunt live tree.

    Returns:
        List of (hcl_file_path, bucket_name) tuples.
    """
    results: list[tuple[pathlib.Path, str]] = []
    for hcl_file in sorted(terragrunt_root.rglob("terragrunt.hcl")):
        if "_envcommon" in hcl_file.parts:
            continue
        account_id = _find_account_id_in_ancestors(hcl_file) or "000000000000"
        namespace = _derive_namespace_from_path(hcl_file, terragrunt_root)
        raw_name = f"{account_id}-{namespace}-tfstate"
        final_name = shorten_bucket_name(
            account_id=account_id,
            namespace=namespace,
            raw_name=raw_name,
        )
        results.append((hcl_file, final_name))
    return results


def run_bucket_name_guard(terragrunt_root: pathlib.Path) -> list[str]:
    """Run the bucket-name-uniqueness guard across all leaf units.

    Args:
        terragrunt_root: Root of the Terragrunt live tree.

    Returns:
        List of error messages (empty if all names are unique and within limit).
    """
    collected = collect_bucket_names(terragrunt_root)
    bucket_names = [name for _, name in collected]
    try:
        assert_bucket_names_unique(bucket_names)
    except (BucketNameCollisionError, BucketNameTooLongError) as exc:
        return [str(exc)]
    return []


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def _get_default_terragrunt_root() -> pathlib.Path | None:
    """Derive the default terragrunt root from the repo structure."""
    import os

    env_root = os.environ.get("TG_LIVE_ROOT", "")
    if env_root:
        return pathlib.Path(env_root)
    repo_root = pathlib.Path(__file__).parent.parent
    candidate = repo_root / "terragrunt"
    return candidate if candidate.exists() else None


def main() -> int:
    """Entry point for `uv run python -m scripts.tg_bucket_name_unique`."""
    terragrunt_root = _get_default_terragrunt_root()
    if terragrunt_root is None or not terragrunt_root.exists():
        print(
            "ERROR: Terragrunt live tree not found.\n"
            "  Set TG_LIVE_ROOT to the path of the terragrunt/ directory.",
            file=sys.stderr,
        )
        return 1

    errors = run_bucket_name_guard(terragrunt_root=terragrunt_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(
            f"\ntg-bucket-name-unique FAILED: {len(errors)} bucket name issue(s) found.",
            file=sys.stderr,
        )
        return 1
    print("tg-bucket-name-unique PASSED: all state bucket names are unique and within 63 chars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

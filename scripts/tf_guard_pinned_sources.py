"""tf_guard_pinned_sources -- rejects leaf module sources not pinned to a semver git ref.

Run via: uv run python -m scripts.tf_guard_pinned_sources

Scans all leaf terragrunt.hcl files under the Terragrunt live tree and rejects any
leaf whose source is not pinned to a '?ref=.../v<semver>' git ref when the governing
account.hcl declares use_pinned_module_sources = true (prod context). When
use_pinned_module_sources = false (dev/sandbox context), a leaf may use the in-repo
'${get_repo_root()}//...' local source.

The guard resolves the per-leaf toggle from the leaf's governing account.hcl (the same
read path the runtime value flows through: account.hcl -> _envcommon/<svc>.hcl -> leaf
inputs). A leaf whose account.hcl lacks the toggle fails fast with MissingToggleError.

Bootstrap path carve-out (D-16):
  Leaves whose path contains a 'bootstrap' segment are exempt from the pinned-source
  requirement regardless of the use_pinned_module_sources toggle. Bootstrap units always
  source the in-repo module via get_repo_root() because they must be applyable before the
  module git tag exists in the remote (pre-push bootstrap flow). The pin toggle governs
  service units only. See docs/terraform-module-sourcing.md for the full sourcing model.

Two source shapes are recognised in leaf terragrunt.hcl files:

  Literal form (legacy / synthetic tests):
    source = "<url>"

  Ternary toggle form (real leaf shape written by E9-F3-S1-T1):
    source = <toggle_expr> ? "<true_branch>" : "<false_branch>"
    e.g. source = local.account_vars.locals.use_pinned_module_sources ?
           "git::...?ref=.../v1.0.0" : "${get_repo_root()}//..."

For the ternary form, the guard evaluates the branch selected by the resolved
account toggle: when toggle=true the true-branch must be a pinned git ref; when
toggle=false the false-branch (local get_repo_root) is allowed.

Toggle-aware decision:
  - ${get_repo_root()} local source: ALLOWED when toggle=false, REJECTED when toggle=true
  - floating branch refs (?ref=main, ?ref=develop): REJECTED (toggle=true only)
  - non-semver refs (?ref=sha1234, ?ref=feature-branch): REJECTED (toggle=true only)
  - sources with no ?ref= at all: REJECTED (toggle=true only)
  - ?ref=<anything>/v<semver>: ALLOWED (always, toggle=true context)

Only '?ref=<anything>/v<semver>' passes for toggle=true context
(e.g. ?ref=v1.2.3 or ?ref=refs/tags/v1.2.3).

Environment variable consumed (optional):
    TG_LIVE_ROOT    -- path to the Terragrunt live tree (default: terragrunt/ relative
                       to the repo root derived from __file__).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to extract the terraform source value from a leaf terragrunt.hcl.
# Matches the literal form: source = "literal_string"
SOURCE_RE = re.compile(r'^\s*source\s*=\s*"([^"]+)"', re.MULTILINE)

# Regex to extract the ternary toggle source form written by E9-F3-S1-T1:
#   source = <toggle_expr> ? "<true_branch>" : "<false_branch>"
# Captures group(1) = true_branch, group(2) = false_branch.
# The toggle expression (before the '?') is intentionally not captured -- only
# the string branches matter for validation.
TERNARY_SOURCE_RE = re.compile(
    r"""^\s*source\s*=\s*[^"?]+\?\s*"([^"]+)"\s*:\s*"([^"]+)"\s*$""",
    re.MULTILINE,
)

# A valid ref must be ?ref=<something>/v<N>.<N>.<N> or ?ref=v<N>.<N>.<N>
# (may have additional patch suffix like -rc1 or -beta.1).
VALID_REF_RE = re.compile(r"\?ref=(?:[^/]*/)*v\d+\.\d+\.\d+")

# A local get_repo_root reference.
LOCAL_REPO_ROOT_RE = re.compile(r"\$\{get_repo_root\(\)\}")

# HCL locals block extraction: matches 'use_pinned_module_sources = true/false'
# within a locals { ... } block in account.hcl.
TOGGLE_RE = re.compile(
    r"use_pinned_module_sources\s*=\s*(true|false)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Source value types
# ---------------------------------------------------------------------------


class LiteralSource(NamedTuple):
    """A terraform source declared as a literal quoted string."""

    value: str


class TernarySource(NamedTuple):
    """A terraform source declared as a ternary toggle expression.

    The true_branch is expected to be a pinned git ref (prod context).
    The false_branch is expected to be a local get_repo_root path (dev/sandbox context).
    """

    true_branch: str
    false_branch: str


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class UnpinnedSourceError(RuntimeError):
    """Raised when a leaf module source is not pinned to a semver git ref."""


class MissingToggleError(RuntimeError):
    """Raised when a leaf's governing account.hcl lacks the use_pinned_module_sources toggle."""


# ---------------------------------------------------------------------------
# Toggle resolution
# ---------------------------------------------------------------------------


def resolve_toggle_from_account_hcl(
    leaf_path: pathlib.Path,
    live_root: pathlib.Path,
) -> bool:
    """Resolve use_pinned_module_sources from the leaf's governing account.hcl.

    Walks up the directory tree from the leaf's directory toward live_root,
    stopping at the first directory that contains an account.hcl file, then
    extracts the use_pinned_module_sources boolean from that file.

    Args:
        leaf_path: Absolute or relative path to the leaf terragrunt.hcl file.
        live_root: Root of the Terragrunt live tree (used as the upper bound for
                   the directory walk).

    Returns:
        True when use_pinned_module_sources = true (prod/pinned context).
        False when use_pinned_module_sources = false (dev/sandbox context).

    Raises:
        MissingToggleError: If no account.hcl is found in any parent directory
            up to live_root, or if account.hcl is found but lacks the
            use_pinned_module_sources key.
    """
    live_root_resolved = live_root.resolve()
    candidate = leaf_path.parent

    # Walk from the leaf toward live_root, checking each directory for account.hcl.
    # The loop visits every ancestor that is a proper descendant-or-equal of live_root,
    # then terminates when the next step would exit that boundary.
    while True:
        candidate_resolved = candidate.resolve()
        # Boundary guard: stop before searching outside live_root.
        try:
            candidate_resolved.relative_to(live_root_resolved)
        except ValueError:
            break

        account_hcl = candidate / "account.hcl"
        if account_hcl.exists():
            return _extract_toggle_from_file(account_hcl)

        next_candidate = candidate.parent
        # Termination guard: stop if ascending would revisit the same directory
        # (happens when candidate is live_root itself -- its parent is outside
        # live_root, so the next iteration's boundary check will break anyway,
        # but we advance to trigger that check cleanly).
        candidate = next_candidate

    raise MissingToggleError(
        f"ERROR: No account.hcl found in any parent directory of {leaf_path}\n"
        f"  Searched from {leaf_path.parent} up to {live_root}.\n"
        f"  Every leaf unit must have a governing account.hcl that declares\n"
        f"  'use_pinned_module_sources' (spec Section 4.3).\n"
        f"  Remedy: add account.hcl with 'use_pinned_module_sources = true|false'\n"
        f"  to the account-level directory under the live tree."
    )


def _extract_toggle_from_file(account_hcl: pathlib.Path) -> bool:
    """Extract the use_pinned_module_sources boolean from an account.hcl file.

    Args:
        account_hcl: Path to the account.hcl file.

    Returns:
        True if use_pinned_module_sources = true, False if false.

    Raises:
        MissingToggleError: If the key is absent from the file.
    """
    content = account_hcl.read_text(encoding="utf-8")
    match = TOGGLE_RE.search(content)
    if match is not None:
        return match.group(1).lower() == "true"
    # Env-keyed layout (env_accounts.json refactor): account.hcl no longer hardcodes the literal
    # boolean; it resolves the toggle dynamically from common/env_accounts.json keyed by the
    # env-class, e.g. `use_pinned_module_sources = local._entry["use_pinned_module_sources"]`.
    # Resolve it the SAME way the runtime does so the guard validates the deployed value instead
    # of failing on the (now intentionally absent) literal.
    if "use_pinned_module_sources" in content and "env_accounts" in content:
        return _resolve_toggle_from_env_accounts(account_hcl)
    raise MissingToggleError(
        f"ERROR: 'use_pinned_module_sources' key not found in {account_hcl}\n"
        f"  Every account.hcl must declare this key with no default (spec Section 4.3),\n"
        f"  either as a literal (use_pinned_module_sources = true|false) or by resolving it\n"
        f"  from common/env_accounts.json (env-keyed layout).\n"
        f"  A missing key is treated as a configuration error and aborts the guard.\n"
        f"  Remedy: add 'use_pinned_module_sources = true' (prod) or\n"
        f"  'use_pinned_module_sources = false' (dev/sandbox) to {account_hcl}, or wire it to\n"
        f"  env_accounts.json."
    )


def _resolve_toggle_from_env_accounts(account_hcl: pathlib.Path) -> bool:
    """Resolve use_pinned_module_sources from common/env_accounts.json for an env-keyed account.hcl.

    The env-keyed layout abstracts the account + toggle out of the folder path: account.hcl reads
    common/env_accounts.json["envs"][<env-class>]. The <env-class> is the basename of the directory
    holding the env-level environment.hcl (whose own `environment = basename(get_terragrunt_dir())`
    is the canonical env-class, and is what singleton account.hcl files read via
    read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment). The guard
    mirrors that exact resolution so it validates the value actually deployed -- including nested
    singleton units (_singletons/pretty, _singletons/shared, ...) whose own directory name is NOT
    the env-class.
    """
    env_class = _resolve_env_class(account_hcl)
    env_accounts_path = _find_env_accounts_json(account_hcl)
    data = json.loads(env_accounts_path.read_text(encoding="utf-8"))
    envs = data.get("envs", {})
    if env_class not in envs:
        raise MissingToggleError(
            f"ERROR: env-class '{env_class}' (from {account_hcl}) is not present in "
            f"{env_accounts_path} under 'envs'.\n"
            f"  Remedy: add an 'envs.{env_class}' row with use_pinned_module_sources before "
            f"deploying this env."
        )
    entry = envs[env_class]
    if "use_pinned_module_sources" not in entry:
        raise MissingToggleError(
            f"ERROR: 'use_pinned_module_sources' missing for env-class '{env_class}' in "
            f"{env_accounts_path}.\n"
            f"  Remedy: set envs.{env_class}.use_pinned_module_sources to true (prod) or "
            f"false (dev/sandbox)."
        )
    return bool(entry["use_pinned_module_sources"])


def _find_env_accounts_json(start: pathlib.Path) -> pathlib.Path:
    """Walk up from an account.hcl to locate terragrunt/common/env_accounts.json."""
    directory = start.parent
    while True:
        candidate = directory / "terragrunt" / "common" / "env_accounts.json"
        if candidate.exists():
            return candidate
        if directory == directory.parent:
            raise MissingToggleError(
                f"ERROR: could not locate terragrunt/common/env_accounts.json above {start}."
            )
        directory = directory.parent


def _resolve_env_class(account_hcl: pathlib.Path) -> str:
    """Return the env-class (e.g. sandbox/prod/qa) governing an account.hcl.

    The env-class is the basename of the directory that holds the env-level environment.hcl,
    found by walking up from the account.hcl. This matches environment.hcl's own
    `environment = basename(get_terragrunt_dir())` and is robust for nested singleton units whose
    own directory name (e.g. 'pretty', 'shared') is NOT the env-class.
    """
    directory = account_hcl.parent
    while True:
        if (directory / "environment.hcl").exists():
            return directory.name
        if directory == directory.parent:
            raise MissingToggleError(
                f"ERROR: no environment.hcl found in any parent of {account_hcl} to resolve "
                f"the env-class for the env_accounts.json toggle lookup."
            )
        directory = directory.parent


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def check_source_string(
    source: str,
    file_path: pathlib.Path,
    pinned_required: bool = True,
) -> None:
    """Assert that a terraform source string is valid for its context.

    When pinned_required=True (prod context):
      - ${get_repo_root()} local sources are REJECTED -- must use pinned git ref.
      - floating branch refs, non-semver refs, and sources without ?ref= are REJECTED.
      - only ?ref=.../v<semver> passes.

    When pinned_required=False (dev/sandbox context):
      - ${get_repo_root()} local sources are ALLOWED.
      - any other source must still be a valid pinned ref if it includes a ?ref=.

    Args:
        source: The raw source string from the terraform block.
        file_path: Path to the terragrunt.hcl file (for error messages).
        pinned_required: True when the leaf's account.hcl declares
            use_pinned_module_sources=true (prod). False when false (dev/sandbox).

    Raises:
        UnpinnedSourceError: If the source is not valid for its declared context.
    """
    if LOCAL_REPO_ROOT_RE.search(source):
        if pinned_required:
            raise UnpinnedSourceError(
                f"ERROR: Unpinned local source in {file_path}\n"
                f"  Source contains '${{get_repo_root()}}' which is a local reference.\n"
                f"  This leaf's account.hcl declares use_pinned_module_sources=true\n"
                f"  (prod context), so all sources must be pinned git refs.\n"
                f"  Source: {source}\n"
                f"  Remedy: replace the local path with a pinned ?ref=.../v<semver> git source."
            )
        # pinned_required=False (dev/sandbox): local source is allowed.
        return

    if "?ref=" not in source:
        raise UnpinnedSourceError(
            f"ERROR: Source has no ?ref= parameter in {file_path}\n"
            f"  All merged leaf sources must be pinned to a semver git ref "
            f"(docs/release-pipeline.md).\n"
            f"  Source: {source}\n"
            f"  Remedy: add ?ref=v<major>.<minor>.<patch> to the source URL."
        )

    if not VALID_REF_RE.search(source):
        ref_match = re.search(r"\?ref=([^&\s]+)", source)
        ref_value = ref_match.group(1) if ref_match else "<unknown>"
        raise UnpinnedSourceError(
            f"ERROR: Source ref '{ref_value}' is not a semver tag in {file_path}\n"
            f"  The ref must match ?ref=.../v<major>.<minor>.<patch> (docs/release-pipeline.md).\n"
            f"  Floating branch refs (main, develop), plain SHAs, and non-versioned refs\n"
            f"  are rejected.\n"
            f"  Source: {source}\n"
            f"  Remedy: pin to an immutable semver tag, e.g. ?ref=v1.2.3."
        )


def collect_leaf_sources(
    terragrunt_root: pathlib.Path,
) -> list[tuple[pathlib.Path, LiteralSource | TernarySource]]:
    """Collect all terraform source values from leaf terragrunt.hcl files.

    Recognises two source declaration shapes:

    1. Literal form:  source = "<url>"
       Returns a LiteralSource wrapping the quoted string.

    2. Ternary toggle form (real leaf shape from E9-F3-S1-T1):
         source = <toggle_expr> ? "<true_branch>" : "<false_branch>"
       Returns a TernarySource with both branch strings extracted.

    Lines that match the ternary form are not re-matched by the literal regex,
    so each source declaration is returned exactly once.

    Only files that are NOT under _envcommon are considered leaf units.

    Args:
        terragrunt_root: Root of the Terragrunt live tree.

    Returns:
        List of (hcl_file_path, source_value) tuples where source_value is
        either a LiteralSource or a TernarySource.
    """
    results: list[tuple[pathlib.Path, LiteralSource | TernarySource]] = []
    for hcl_file in terragrunt_root.rglob("terragrunt.hcl"):
        # Skip _envcommon templates -- they are not leaf units.
        if "_envcommon" in hcl_file.parts:
            continue
        # Skip bootstrap units (D-16): bootstrap leaves always use the in-repo
        # module source via get_repo_root() regardless of use_pinned_module_sources.
        # The pin toggle governs service units only.
        if "bootstrap" in hcl_file.parts:
            continue
        content = hcl_file.read_text(encoding="utf-8")

        # Try the ternary form first (real leaf shape from E9-F3-S1-T1).
        # The ternary regex requires a non-quoted token before '?', so a plain
        # source = "literal" line cannot match it; the two patterns are disjoint.
        for match in TERNARY_SOURCE_RE.finditer(content):
            results.append(
                (hcl_file, TernarySource(true_branch=match.group(1), false_branch=match.group(2)))
            )

        # Try the literal form (legacy / synthetic test fixtures).
        for match in SOURCE_RE.finditer(content):
            results.append((hcl_file, LiteralSource(value=match.group(1))))
    return results


def run_source_guard(terragrunt_root: pathlib.Path) -> list[str]:
    """Run the pinned-sources guard across all leaf units.

    Resolves the use_pinned_module_sources toggle from each leaf's governing
    account.hcl and applies context-aware validation:
      - toggle=false (dev/sandbox): ${get_repo_root()} local sources are allowed.
      - toggle=true (prod): all sources must be pinned ?ref=.../v<semver> refs.

    Handles both LiteralSource and TernarySource values returned by collect_leaf_sources:
      - LiteralSource: validated directly against the resolved toggle.
      - TernarySource: the branch selected by the toggle is validated
        (true_branch when toggle=true, false_branch when toggle=false).

    Args:
        terragrunt_root: Root of the Terragrunt live tree.

    Returns:
        List of error messages (empty if all sources are valid for their context).

    Raises:
        MissingToggleError: If any leaf's account.hcl lacks use_pinned_module_sources.
    """
    errors: list[str] = []
    for hcl_file, source_value in collect_leaf_sources(terragrunt_root):
        pinned_required = resolve_toggle_from_account_hcl(
            leaf_path=hcl_file,
            live_root=terragrunt_root,
        )
        # Select the effective source string based on the source shape.
        if isinstance(source_value, TernarySource):
            # For ternary leaves: evaluate the branch the toggle selects.
            # toggle=true (prod) -> the true_branch must be a pinned git ref.
            # toggle=false (dev/sandbox) -> the false_branch (local) is allowed.
            effective_source = (
                source_value.true_branch if pinned_required else source_value.false_branch
            )
        else:
            effective_source = source_value.value
        try:
            check_source_string(
                source=effective_source,
                file_path=hcl_file,
                pinned_required=pinned_required,
            )
        except UnpinnedSourceError as exc:
            errors.append(str(exc))
    return errors


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def _get_default_terragrunt_root() -> pathlib.Path | None:
    """Derive the default terragrunt root from the repo structure."""
    env_root = os.environ.get("TG_LIVE_ROOT", "")
    if env_root:
        return pathlib.Path(env_root)
    repo_root = pathlib.Path(__file__).parent.parent
    candidate = repo_root / "terragrunt"
    return candidate if candidate.exists() else None


def main() -> int:
    """Entry point for `uv run python -m scripts.tf_guard_pinned_sources`."""
    terragrunt_root = _get_default_terragrunt_root()
    if terragrunt_root is None or not terragrunt_root.exists():
        print(
            "ERROR: Terragrunt live tree not found.\n"
            "  Set TG_LIVE_ROOT to the path of the terragrunt/ directory.",
            file=sys.stderr,
        )
        return 1

    try:
        errors = run_source_guard(terragrunt_root=terragrunt_root)
    except MissingToggleError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "\ntf-guard-pinned-sources FAILED: missing use_pinned_module_sources toggle.",
            file=sys.stderr,
        )
        return 1

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(
            f"\ntf-guard-pinned-sources FAILED: {len(errors)} unpinned source(s) found.",
            file=sys.stderr,
        )
        return 1
    print("tf-guard-pinned-sources PASSED: all leaf sources are valid for their context.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

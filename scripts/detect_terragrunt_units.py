"""detect_terragrunt_units -- maps changed files to Terragrunt unit dirs and emits queue flags.

Run via: uv run python -m scripts.detect_terragrunt_units

Maps each changed file (from BASE..HEAD diff) to its enclosing Terragrunt unit dir,
de-duplicates multiple changed files that resolve to the same unit, includes the
dependent units in the DAG, and emits the result to the OUTPUT file as the
'include_dir_flags' output variable using the Terragrunt 1.0.7 --queue-include-dir
token (never the removed --terragrunt-include-dir token per D35, AC-5).

Aborts (exit 1) when the computed unit scope is empty rather than emitting a no-op
apply (D3, AC-5). The one exception in the PR-PLAN path is a DELETION-ONLY changeset:
when every changed file is a deletion (the deleted units' live resources are already
destroyed, so there is nothing to plan) the empty scope is a clean no-op (empty
include_dir_flags + has_units=false), not a D3 failure. A genuine "changed files point
outside any unit" empty scope still fails closed.

CLI arguments (all required):
    --base               BASE git ref for the diff (e.g. origin/main)
    --head               HEAD git ref for the diff (e.g. HEAD)
    --terragrunt-root    path to the terragrunt/ live tree root directory
    --output             path to the GitHub Actions output file ($GITHUB_OUTPUT)

Output variable written:
    include_dir_flags    space-separated list of '--queue-include-dir <unit>' pairs
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The Terragrunt 1.0.7 token for specifying a unit to include (D35, AC-5).
QUEUE_INCLUDE_DIR_TOKEN = "--queue-include-dir"

# The removed v0.x token that must NEVER be emitted (D35).
REMOVED_INCLUDE_DIR_TOKEN = "--terragrunt-include-dir"

# Regex to extract dependency config_path values from terragrunt.hcl content.
DEPENDENCY_CONFIG_PATH_RE = re.compile(
    r'dependency\s+"[^"]+"\s*\{[^}]*config_path\s*=\s*"([^"]+)"',
    re.DOTALL,
)

# A unit consumes shared parent configs through three reference forms. These regexes
# extract each so a change to a shared parent config (service.hcl / account.hcl /
# environment.hcl / _envcommon/*.hcl / root.hcl / ...) can be mapped to every unit
# that includes or reads it (issue #91 -- parent-config blind spot).
#
# 1. find_in_parent_folders("<name>") -> the NEAREST ancestor file named <name>
#    (include "root", read_terragrunt_config(find_in_parent_folders("service.hcl")), ...).
FIND_IN_PARENT_FOLDERS_RE = re.compile(r'find_in_parent_folders\(\s*"([^"]+)"\s*\)')

# 2. ${dirname(find_in_parent_folders("<anchor>"))}/<rest> -> a repo-root-anchored path,
#    the form the _envcommon includes use (anchor is root.hcl; rest is /_envcommon/<x>.hcl).
DIRNAME_PARENT_FOLDERS_REF_RE = re.compile(
    r'\$\{dirname\(find_in_parent_folders\(\s*"([^"]+)"\s*\)\)\}((?:/[^"\s]+)+)'
)

# 3. ${get_terragrunt_dir()}/<rest> -> a unit-dir-relative read
#    (read_terragrunt_config("${get_terragrunt_dir()}/../../../active.hcl"), ...).
GET_TERRAGRUNT_DIR_REF_RE = re.compile(r'\$\{get_terragrunt_dir\(\)\}((?:/[^"\s]+)+)')

# Output key for GitHub Actions output variable.
OUTPUT_KEY_INCLUDE_DIR_FLAGS = "include_dir_flags"

# Output key signalling whether any applyable unit remains in scope. Only emitted in
# --exclude-bootstrap mode so a push that touched ONLY bootstrap/common files is a clean
# CI-apply no-op (bootstrap units are operator-applied out-of-band per D40/D33/D-15) instead
# of a hard failure.
OUTPUT_KEY_HAS_UNITS = "has_units"

# Path segment marking a per-account bootstrap unit (bootstrap/<role>_role/...). Bootstrap units
# are applied ONCE by the operator out-of-band (D40: the OIDC provider is an operator
# prerequisite; D33: sandbox is local-only; D-15: state-bootstrap first-apply is manual). CI must
# never apply them, so the apply path excludes them from the change-scoped apply (the PR PLAN path
# still validates/plans them -- exclusion is apply-only).
_BOOTSTRAP_PATH_SEGMENT = "/bootstrap/"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class EmptyUnitScopeError(RuntimeError):
    """Raised when no unit dirs are found for the given changed files (D3)."""


class ForbiddenTokenError(RuntimeError):
    """Raised when a forbidden CLI token (e.g. --terragrunt-include-dir) is detected."""


# ---------------------------------------------------------------------------
# Core logic functions (pure -- no subprocess calls for testability)
# ---------------------------------------------------------------------------


def detect_unit_dir(
    changed_file: pathlib.Path,
    terragrunt_root: pathlib.Path,
) -> pathlib.Path | None:
    """Map a changed file to its enclosing Terragrunt unit directory.

    A Terragrunt unit directory is any directory containing a terragrunt.hcl
    file. Walks upward from the changed file's parent until a terragrunt.hcl
    is found or the terragrunt_root is reached.

    Args:
        changed_file: Absolute path to the changed file.
        terragrunt_root: Root of the Terragrunt live tree (search stops here).

    Returns:
        The enclosing unit directory path, or None if no unit is found.
    """
    candidate = changed_file if changed_file.is_dir() else changed_file.parent
    while True:
        if (candidate / "terragrunt.hcl").exists():
            return candidate
        if candidate == terragrunt_root or candidate.parent == candidate:
            return None
        candidate = candidate.parent


def parse_dependency_paths(hcl_file: pathlib.Path) -> list[str]:
    """Extract all dependency config_path values from a terragrunt.hcl file.

    Args:
        hcl_file: Path to the terragrunt.hcl file to parse.

    Returns:
        A list of raw config_path strings (may be relative paths).
    """
    content = hcl_file.read_text(encoding="utf-8")
    return DEPENDENCY_CONFIG_PATH_RE.findall(content)


def find_dependent_units(
    changed_units: set[pathlib.Path],
    terragrunt_root: pathlib.Path,
) -> set[pathlib.Path]:
    """Find all units in the live tree that declare a dependency on any changed unit.

    Resolves relative config_path values from each candidate unit's directory
    and checks whether the resolved path matches any changed unit.

    Args:
        changed_units: Set of unit directory paths that were directly changed.
        terragrunt_root: Root of the Terragrunt live tree to scan.

    Returns:
        Set of unit directories that depend on at least one changed unit.
    """
    dependents: set[pathlib.Path] = set()
    for hcl_file in terragrunt_root.rglob("terragrunt.hcl"):
        unit_dir = hcl_file.parent
        if unit_dir in changed_units:
            continue
        dep_paths = parse_dependency_paths(hcl_file)
        for raw_dep_path in dep_paths:
            resolved = (unit_dir / raw_dep_path).resolve()
            for changed_unit in changed_units:
                if resolved == changed_unit.resolve():
                    dependents.add(unit_dir)
                    break
    return dependents


def _resolve_in_parent_folders(
    name: str,
    unit_dir: pathlib.Path,
    terragrunt_root: pathlib.Path,
) -> pathlib.Path | None:
    """Resolve ``find_in_parent_folders(name)`` from ``unit_dir``.

    Mirrors Terragrunt: search the ANCESTOR directories of the unit (not the unit
    dir itself) walking up and return the first file named ``name``. The search is
    bounded by ``terragrunt_root`` (which is itself checked, so ``root.hcl`` at the
    tree root resolves) and never escapes above it.

    Args:
        name: The config filename to find (e.g. ``service.hcl``).
        unit_dir: The unit directory the reference is evaluated from.
        terragrunt_root: Root of the Terragrunt live tree (search upper bound).

    Returns:
        The resolved absolute path, or None when no ancestor declares ``name``.
    """
    candidate = unit_dir.parent
    while True:
        candidate_file = candidate / name
        if candidate_file.exists():
            return candidate_file.resolve()
        if candidate == terragrunt_root or candidate.parent == candidate:
            return None
        candidate = candidate.parent


def resolve_parent_config_references(
    hcl_file: pathlib.Path,
    terragrunt_root: pathlib.Path,
) -> set[pathlib.Path]:
    """Return the set of absolute shared-parent-config paths a unit references.

    Resolves the three reference forms a terragrunt unit uses to consume shared
    parent configs (see the module regexes). References whose interpolation cannot
    be resolved statically (e.g. a ``${local.*}`` segment that points at another
    unit, not a shared config) are skipped -- those are handled by the dependency
    DAG, not by parent-config mapping.

    Args:
        hcl_file: Path to a unit's terragrunt.hcl.
        terragrunt_root: Root of the Terragrunt live tree.

    Returns:
        Absolute, resolved paths of every shared parent config the unit references.
    """
    unit_dir = hcl_file.parent
    content = hcl_file.read_text(encoding="utf-8")
    refs: set[pathlib.Path] = set()

    for name in FIND_IN_PARENT_FOLDERS_RE.findall(content):
        resolved = _resolve_in_parent_folders(name, unit_dir, terragrunt_root)
        if resolved is not None:
            refs.add(resolved)

    for anchor, rest in DIRNAME_PARENT_FOLDERS_REF_RE.findall(content):
        anchor_path = _resolve_in_parent_folders(anchor, unit_dir, terragrunt_root)
        if anchor_path is not None:
            refs.add((anchor_path.parent / rest.lstrip("/")).resolve())

    for rest in GET_TERRAGRUNT_DIR_REF_RE.findall(content):
        if "${" in rest:
            # An unresolved nested interpolation (e.g. ${local.bootstrap_role}); this
            # targets another unit, not a shared parent config -- skip it.
            continue
        refs.add((unit_dir / rest.lstrip("/")).resolve())

    return refs


def find_units_affected_by_parent_config(
    config_file: pathlib.Path,
    terragrunt_root: pathlib.Path,
) -> set[pathlib.Path]:
    """Return every unit whose terragrunt.hcl includes or reads ``config_file``.

    A changed shared parent config (service.hcl / account.hcl / environment.hcl /
    _envcommon/*.hcl / root.hcl / ...) lives OUTSIDE any unit directory, so
    ``detect_unit_dir`` maps it to nothing even though it is included/read by the
    child units that inherit it. Scanning every unit and resolving its references
    closes that blind spot (issue #91): the change is applied to every unit it
    actually affects, never silently dropped from the scope.

    Args:
        config_file: The changed shared parent config (absolute path).
        terragrunt_root: Root of the Terragrunt live tree to scan.

    Returns:
        Set of unit directories that reference ``config_file``.
    """
    config_resolved = config_file.resolve()
    affected: set[pathlib.Path] = set()
    for hcl_file in terragrunt_root.rglob("terragrunt.hcl"):
        if config_resolved in resolve_parent_config_references(hcl_file, terragrunt_root):
            affected.add(hcl_file.parent)
    return affected


def _is_shared_parent_config(
    changed_file: pathlib.Path,
    terragrunt_root: pathlib.Path,
) -> bool:
    """Return True when a non-unit changed file is a shared Terragrunt parent config.

    Shared parent configs are the ``.hcl`` files (root.hcl, env*.hcl, service*.hcl,
    account.hcl, _envcommon/*.hcl, common/*.hcl, active.hcl, ...) that live under the
    Terragrunt tree OUTSIDE any unit directory; they are included/read by child units
    rather than being a unit (a ``terragrunt.hcl``) themselves.

    Args:
        changed_file: A changed file that did NOT map to a unit directory.
        terragrunt_root: Root of the Terragrunt live tree.

    Returns:
        True when the file is an ``.hcl`` config under the tree (and not a
        ``terragrunt.hcl`` unit definition).
    """
    if changed_file.name == "terragrunt.hcl" or changed_file.suffix != ".hcl":
        return False
    try:
        changed_file.resolve().relative_to(terragrunt_root.resolve())
    except ValueError:
        return False
    return True


def build_include_dir_flags(unit_dirs: list[pathlib.Path]) -> list[str]:
    """Build the --queue-include-dir flags list for the given unit directories.

    Emits the Terragrunt 1.0.7 --queue-include-dir token for each unit.
    Never emits the removed --terragrunt-include-dir token (D35, AC-5).
    Raises EmptyUnitScopeError if unit_dirs is empty (D3).

    Args:
        unit_dirs: List of unit directory absolute paths.

    Returns:
        A flat list alternating [--queue-include-dir, <unit>, ...].

    Raises:
        EmptyUnitScopeError: If unit_dirs is empty.
    """
    if not unit_dirs:
        raise EmptyUnitScopeError(
            "ERROR: No Terragrunt unit directories found for the changed files.\n"
            "  An empty unit scope would result in a no-op apply, which is rejected (D3).\n"
            "  Remedy: verify that the changed files belong to a Terragrunt unit directory\n"
            "  (a directory containing a terragrunt.hcl file)."
        )
    flags: list[str] = []
    for unit_dir in unit_dirs:
        flags.extend([QUEUE_INCLUDE_DIR_TOKEN, str(unit_dir)])
    return flags


# ---------------------------------------------------------------------------
# Git diff helper
# ---------------------------------------------------------------------------


def _run_git_diff_name_only(
    base_ref: str,
    head_ref: str,
    repo_root: pathlib.Path,
    diff_filter: str | None = None,
) -> list[pathlib.Path]:
    """Run ``git diff --name-only [--diff-filter=<f>] BASE...HEAD`` and return absolute paths.

    Args:
        base_ref: The base git ref (e.g. origin/main).
        head_ref: The head git ref (e.g. HEAD).
        repo_root: The root of the git repository.
        diff_filter: Optional ``--diff-filter`` selector (e.g. ``D`` for deletions only);
            omitted for the full changed-file set.

    Returns:
        List of absolute paths to the matching files.

    Raises:
        RuntimeError: If git diff fails.
    """
    cmd = ["git", "diff", "--name-only"]
    if diff_filter is not None:
        cmd.append(f"--diff-filter={diff_filter}")
    cmd.append(f"{base_ref}...{head_ref}")
    result = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: git diff failed (exit {result.returncode}).\n"
            f"  Command: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  Remedy: ensure both refs exist and the git repo is available."
        )
    paths: list[pathlib.Path] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            paths.append(repo_root / stripped)
    return paths


def list_changed_files(
    base_ref: str,
    head_ref: str,
    repo_root: pathlib.Path,
) -> list[pathlib.Path]:
    """List files changed between base_ref and head_ref using git diff.

    Args:
        base_ref: The base git ref (e.g. origin/main).
        head_ref: The head git ref (e.g. HEAD).
        repo_root: The root of the git repository.

    Returns:
        List of absolute paths to changed files.

    Raises:
        RuntimeError: If git diff fails.
    """
    return _run_git_diff_name_only(base_ref, head_ref, repo_root)


def list_deleted_files(
    base_ref: str,
    head_ref: str,
    repo_root: pathlib.Path,
) -> list[pathlib.Path]:
    """List files DELETED between base_ref and head_ref (``git diff --diff-filter=D``).

    Args:
        base_ref: The base git ref (e.g. origin/main).
        head_ref: The head git ref (e.g. HEAD).
        repo_root: The root of the git repository.

    Returns:
        List of absolute paths to files removed in the diff range.

    Raises:
        RuntimeError: If git diff fails.
    """
    return _run_git_diff_name_only(base_ref, head_ref, repo_root, diff_filter="D")


def is_deletion_only_changeset(
    changed_files: list[pathlib.Path],
    deleted_files: list[pathlib.Path],
) -> bool:
    """Return True when the changeset is non-empty and every changed file was deleted.

    A deletion-only terragrunt PR removes units whose live resources are already
    destroyed; there is nothing left to plan, so an empty resulting unit scope is a
    legitimate no-op rather than the D3 fail-closed case. An empty changeset is NOT
    deletion-only (there is nothing to classify), and a changeset that mixes deletions
    with additions/modifications is NOT deletion-only either -- both keep failing closed.

    Args:
        changed_files: Every file changed in the diff range (added/modified/deleted).
        deleted_files: The subset of changed files that were deleted.

    Returns:
        True iff ``changed_files`` is non-empty and equals ``deleted_files`` as a set.
    """
    if not changed_files:
        return False
    return set(changed_files) == set(deleted_files)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def _is_bootstrap_unit(unit_dir: pathlib.Path) -> bool:
    """Return True when the unit is a per-account bootstrap unit (operator-applied, D40/D33/D-15).

    The check is path-based so it is independent of the diff range: any unit under a
    ``.../live/.../bootstrap/<role>_role/...`` path is a bootstrap unit. A trailing-slash-bounded
    match avoids matching an unrelated path component that merely contains 'bootstrap'.
    """
    return _BOOTSTRAP_PATH_SEGMENT in f"/{unit_dir.as_posix().strip('/')}/"


def run_detect(
    base_ref: str,
    head_ref: str,
    terragrunt_root: pathlib.Path,
    output_path: str,
    exclude_bootstrap: bool = False,
) -> None:
    """Detect changed Terragrunt units and write include_dir_flags to output.

    Args:
        base_ref: The base git ref.
        head_ref: The head git ref.
        terragrunt_root: Root of the Terragrunt live tree.
        output_path: Path to the GitHub Actions output file.
        exclude_bootstrap: When True (the CI APPLY path), drop bootstrap units from the scope --
            they are operator-applied out-of-band (D40/D33/D-15) and CI must never apply them. In
            this mode an empty resulting scope is NOT a failure: it is a clean no-op (e.g. a push
            that only touched bootstrap/common files), signalled via has_units=false. The PR PLAN
            path leaves this False so bootstrap units are still planned and an empty scope fails
            closed (D3) -- except for a DELETION-ONLY changeset (every changed file is a deletion),
            whose deleted units' live resources are already destroyed: that empty scope is a clean
            no-op (empty include_dir_flags + has_units=false), not a D3 failure.

    Raises:
        EmptyUnitScopeError: If no units are found AND exclude_bootstrap is False AND the changeset
            is not deletion-only (D3).
        RuntimeError: If git diff fails.
    """
    repo_root = terragrunt_root.parent
    changed_files = list_changed_files(base_ref, head_ref, repo_root)

    changed_units: set[pathlib.Path] = set()
    for changed_file in changed_files:
        unit_dir = detect_unit_dir(changed_file, terragrunt_root=terragrunt_root)
        if unit_dir is not None:
            changed_units.add(unit_dir)
        elif _is_shared_parent_config(changed_file, terragrunt_root):
            # A shared parent config (service.hcl / _envcommon / account.hcl / ...) is not a
            # unit itself, but every unit that includes or reads it is affected by the change.
            # Map it to those units so the change is never silently dropped from the scope
            # (issue #91 -- parent-config blind spot).
            affected = find_units_affected_by_parent_config(changed_file, terragrunt_root)
            if affected:
                print(
                    f"detect-units: parent-config change {changed_file} maps to "
                    f"{len(affected)} dependent unit(s)."
                )
            changed_units |= affected

    dependent_units = find_dependent_units(
        changed_units=changed_units,
        terragrunt_root=terragrunt_root,
    )
    all_units = changed_units | dependent_units

    from scripts.constants import write_output

    if exclude_bootstrap:
        non_bootstrap = {u for u in all_units if not _is_bootstrap_unit(u)}
        excluded = sorted(str(u) for u in (all_units - non_bootstrap))
        if excluded:
            print(
                f"detect-units: excluding {len(excluded)} bootstrap unit(s) from the CI apply "
                f"scope (operator-applied out-of-band, D40/D33/D-15): {', '.join(excluded)}"
            )
        sorted_units = sorted(non_bootstrap, key=lambda p: str(p))
        if not sorted_units:
            # Bootstrap-only / common-only change: a legitimate CI-apply no-op, not a failure.
            write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, "")
            write_output(output_path, OUTPUT_KEY_HAS_UNITS, "false")
            print(
                "detect-units: 0 applyable unit(s) after bootstrap exclusion -- CI apply is a "
                "no-op for this change (has_units=false)."
            )
            return
        flags_str = " ".join(build_include_dir_flags(sorted_units))
        write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, flags_str)
        write_output(output_path, OUTPUT_KEY_HAS_UNITS, "true")
        print(
            f"detect-units: {len(sorted_units)} applyable unit(s) detected (bootstrap excluded). "
            f"include_dir_flags written to {output_path}."
        )
        return

    sorted_units = sorted(all_units, key=lambda p: str(p))
    if not sorted_units:
        # A deletion-only PR removes units whose live resources are already destroyed, so an
        # empty resulting scope has nothing to plan: emit a clean no-op (empty include_dir_flags +
        # has_units=false), mirroring the apply-path bootstrap-only no-op above. A genuine
        # "changed files point outside any unit" empty scope is NOT deletion-only and still fails
        # closed via build_include_dir_flags below (D3, unchanged).
        deleted_files = list_deleted_files(base_ref, head_ref, repo_root)
        if is_deletion_only_changeset(changed_files, deleted_files):
            write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, "")
            write_output(output_path, OUTPUT_KEY_HAS_UNITS, "false")
            print(
                "detect-units: deletion-only changeset with no remaining units in scope -- "
                "CI plan is a no-op for this change (has_units=false)."
            )
            return

    flags = build_include_dir_flags(sorted_units)
    flags_str = " ".join(flags)

    write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, flags_str)
    print(
        f"detect-units: {len(all_units)} unit(s) detected. "
        f"include_dir_flags written to {output_path}."
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Detect changed Terragrunt units and emit --queue-include-dir flags."
    )
    parser.add_argument("--base", required=True, help="Base git ref for the diff.")
    parser.add_argument("--head", required=True, help="Head git ref for the diff.")
    parser.add_argument(
        "--terragrunt-root", required=True, help="Path to the Terragrunt live tree root."
    )
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    parser.add_argument(
        "--exclude-bootstrap",
        action="store_true",
        help=(
            "Drop bootstrap units from the scope (CI APPLY path: bootstrap is operator-applied "
            "out-of-band per D40/D33/D-15). Emits has_units=false instead of failing when the "
            "scope is empty after exclusion. Omit for the PR PLAN path."
        ),
    )
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for `uv run python -m scripts.detect_terragrunt_units`."""
    args = _parse_args(sys.argv[1:])
    terragrunt_root = pathlib.Path(args.terragrunt_root).resolve()

    if not terragrunt_root.exists():
        print(
            f"ERROR: Terragrunt root does not exist: {terragrunt_root}\n"
            f"  Remedy: pass a valid --terragrunt-root path.",
            file=sys.stderr,
        )
        return 1

    try:
        run_detect(
            base_ref=args.base,
            head_ref=args.head,
            terragrunt_root=terragrunt_root,
            output_path=args.output,
            exclude_bootstrap=args.exclude_bootstrap,
        )
    except EmptyUnitScopeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""check_required_var_wiring -- guard against a module required-variable added
without corresponding terragrunt leaf wiring.

Motivation (issue #235): a module-only PR that ADDS a new REQUIRED variable (no
``default``) to a reference/primitive module can pass CI even when no terragrunt
leaf wires the new input, because a module-only changeset does not trigger the
terragrunt plan lane. The unwired required variable then breaks ``terragrunt
plan``/``apply`` for every leaf that sources the module. This guard closes that
gap by statically detecting the precise pattern at PR time.

Precise, fail-closed pattern (conservative by design -- a false positive would
block a legitimate PR, so every ambiguous case PASSES):

For each changed module-level ``variables.tf`` (only ``providers/aws/**`` module
leaves; ``examples/`` and ``tests/`` are ignored):
  1. Parse the base and HEAD revisions of the file. A variable is NEWLY ADDED +
     REQUIRED when it is present in HEAD, absent in base, and declares no
     ``default`` (``default = null`` / ``default = ""`` both count as optional).
  2. Find the terragrunt leaves under ``terragrunt/live/**`` whose
     ``terraform { source = ... }`` sources that module. If the module has no
     consuming leaves, PASS (nothing to wire).
  3. For each consuming leaf, the variable must be wired -- the variable name
     must appear in the leaf's ``terragrunt.hcl`` or in any ``_envcommon/*.hcl``
     template it includes. If a new required variable is unwired in a consuming
     leaf, FAIL with an actionable message naming the module, the variable, and
     the exact leaf file(s) that must wire it.

If diff/HCL parsing is ambiguous (unbalanced braces, unreadable revision), the
affected module is skipped (PASS) so the guard never blocks on parser
uncertainty.

Usage (CI):
    uv run python -m scripts.check_required_var_wiring \\
        --config monorepo-config.json --diff-range 'origin/main...HEAD'

Exit codes:
    0 -- no unwired new required variables detected (PASS)
    1 -- at least one new required variable is unwired in a consuming leaf (FAIL)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.constants import GH_ERROR_PREFIX
from scripts.detect_scope import load_config, parse_scope_config

# The module-level variables file name that declares a module's input contract.
_VARIABLES_FILENAME = "variables.tf"

# Terragrunt live-tree marker (consuming leaves live under terragrunt/live/**).
_TERRAGRUNT_LIVE_SEGMENT = "/live/"

# Terragrunt _envcommon marker (shared input templates included by leaves).
_ENVCOMMON_SEGMENT = "/_envcommon/"


class HclParseError(Exception):
    """Raised when an HCL file cannot be parsed unambiguously.

    Callers treat this as "skip / PASS" -- the guard must never block a PR on
    parser uncertainty (issue #235 design: conservative, no false positives).
    """


@dataclass(frozen=True)
class Finding:
    """A single new-required-variable-without-leaf-wiring violation."""

    module: str
    variable: str
    unwired_leaves: tuple[str, ...]

    def message(self) -> str:
        """Return an actionable, single-line error message for this finding."""
        leaves = ", ".join(self.unwired_leaves)
        return (
            f"{GH_ERROR_PREFIX}Module {self.module!r} adds a new REQUIRED variable "
            f"{self.variable!r} (no default), but the following consuming terragrunt "
            f"leaf/leaves do not wire it: {leaves}. "
            f"Wire {self.variable!r} into each leaf's `inputs` (or the shared "
            f"_envcommon template it includes) in this same PR, or give the variable "
            f"a `default`. See issue #235."
        )


# ---------------------------------------------------------------------------
# HCL masking + variable-block parsing
# ---------------------------------------------------------------------------


def _mask_hcl(text: str) -> str:
    """Return ``text`` with comment and string-literal CONTENT replaced by spaces.

    Line comments (``#`` and ``//``), block comments (``/* */``) and
    double-quoted string contents are blanked so that subsequent brace counting
    and keyword detection operate only on real HCL code. Newlines and character
    positions are preserved (each replaced character becomes a single space),
    so indices into the masked string map 1:1 onto the original.

    String delimiters (the surrounding ``"``) are preserved; only the content
    between them is blanked. This keeps ``variable "name" {`` structurally
    intact while neutralising braces/keywords that appear inside descriptions
    or regex validation strings (e.g. ``{1,64}``).
    """
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        # Line comment: '#' ... EOL
        if c == "#":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        # Line comment: '//' ... EOL
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        # Block comment: /* ... */
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            # Blank the closing */ if present.
            if i < n:
                out[i] = " "
            if i + 1 < n:
                out[i + 1] = " "
            i += 2
            continue
        # Double-quoted string: preserve delimiters, blank the content.
        if c == '"':
            i += 1  # keep opening quote
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                    continue
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            # Leave the closing quote in place (i points at it or at EOF).
            i += 1
            continue
        i += 1
    return "".join(out)


# A variable declaration header on masked text: the name content is blanked, so
# we match the structural shape and recover the real name from the original text.
_VARIABLE_HEADER = re.compile(r'variable\s+"[^"\n]*"\s*\{')
_VARIABLE_NAME = re.compile(r'variable\s+"([^"\n]*)"\s*\{')
_DEFAULT_TOKEN = re.compile(r"[A-Za-z0-9_]")


def parse_variable_blocks(text: str) -> dict[str, bool]:
    """Parse Terraform ``variable`` blocks into ``{name: has_default}``.

    ``has_default`` is True when the variable declares a top-level ``default``
    attribute (any value, including ``null`` or ``""``) -- such a variable is
    optional. A variable with no top-level ``default`` is required.

    Args:
        text: The raw ``variables.tf`` content.

    Returns:
        Mapping of variable name -> whether it declares a top-level default.

    Raises:
        HclParseError: If a variable block has unbalanced braces (ambiguous
            parse). Callers treat this as skip / PASS.
    """
    masked = _mask_hcl(text)
    result: dict[str, bool] = {}

    for header in _VARIABLE_HEADER.finditer(masked):
        name_match = _VARIABLE_NAME.search(text[header.start() : header.end()])
        if name_match is None:
            # The masked header matched but the original span has no real name
            # (should not happen for well-formed HCL); treat as ambiguous.
            raise HclParseError("Could not recover variable name from declaration header.")
        name = name_match.group(1)

        depth = 1  # we are just past the opening '{'
        has_default = False
        i = header.end()
        while i < len(masked) and depth > 0:
            ch = masked[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif depth == 1 and ch == "d" and masked.startswith("default", i):
                before_ok = i == 0 or not _DEFAULT_TOKEN.match(masked[i - 1])
                after_idx = i + len("default")
                after = masked[after_idx] if after_idx < len(masked) else ""
                if before_ok and not _DEFAULT_TOKEN.match(after):
                    j = after_idx
                    while j < len(masked) and masked[j] in " \t":
                        j += 1
                    if j < len(masked) and masked[j] == "=":
                        has_default = True
            i += 1

        if depth != 0:
            raise HclParseError(
                f"Unbalanced braces while parsing variable {name!r}; cannot classify safely."
            )
        result[name] = has_default

    return result


def new_required_variables(base_text: str | None, head_text: str) -> list[str]:
    """Return the names of variables that are newly added AND required in HEAD.

    A variable qualifies when it is present in HEAD with no top-level default and
    was absent from base. ``base_text`` is None when the file did not exist at
    base (every required HEAD variable is then new).

    If either revision cannot be parsed unambiguously, an empty list is returned
    (skip / PASS) -- the guard never blocks on parser uncertainty.
    """
    try:
        head_vars = parse_variable_blocks(head_text)
    except HclParseError:
        return []

    if base_text is None:
        base_names: set[str] = set()
    else:
        try:
            base_names = set(parse_variable_blocks(base_text))
        except HclParseError:
            return []

    return sorted(
        name
        for name, has_default in head_vars.items()
        if not has_default and name not in base_names
    )


# ---------------------------------------------------------------------------
# Module-path + terragrunt consumer resolution
# ---------------------------------------------------------------------------


def module_leaf_for_variables_file(file_path: str, module_roots: list[str]) -> str | None:
    """Return the module leaf directory if ``file_path`` is a module-level variables.tf.

    Only the module leaf's own ``variables.tf`` (``<root>/<leaf>/variables.tf``)
    qualifies. Nested ``variables.tf`` files under ``examples/`` or ``tests/``
    (or any deeper path) return None and are ignored.
    """
    normalized = file_path.replace("\\", "/")
    for root in module_roots:
        root = root if root.endswith("/") else root + "/"
        if not normalized.startswith(root):
            continue
        remainder = normalized[len(root) :]
        parts = remainder.split("/")
        # Exactly <leaf>/variables.tf -> the module-level variables file.
        if len(parts) == 2 and parts[1] == _VARIABLES_FILENAME and parts[0]:
            return root.rstrip("/") + "/" + parts[0]
        return None
    return None


def _module_source_pattern(module_leaf: str) -> re.Pattern[str]:
    """Compile a boundary-safe pattern matching a ``//<module_leaf>`` source ref.

    The trailing negative lookahead prevents ``analytics`` from matching a
    sibling module such as ``analytics-dashboard``.
    """
    return re.compile(r"//" + re.escape(module_leaf) + r"(?![A-Za-z0-9_/-])")


def leaf_sources_module(leaf_text: str, module_leaf: str) -> bool:
    """Return True if ``leaf_text`` has a ``source = ...`` assignment for the module.

    Only assignments whose key is exactly ``source`` count -- child-module
    override keys such as ``spice_kms_source`` (which reference primitives, not
    the leaf's top-level module) are excluded via the ``(?<![\\w-])`` lookbehind.
    """
    pattern = _module_source_pattern(module_leaf)
    for raw_line in leaf_text.splitlines():
        if not re.search(r"(?<![\w-])source\s*=", raw_line):
            continue
        if pattern.search(raw_line):
            return True
    return False


_ENVCOMMON_INCLUDE = re.compile(r"_envcommon/([A-Za-z0-9._-]+\.hcl)")


def envcommon_includes(leaf_text: str) -> list[str]:
    """Return the ``_envcommon/*.hcl`` template basenames referenced by a leaf."""
    return sorted(set(_ENVCOMMON_INCLUDE.findall(leaf_text)))


def variable_is_wired(variable: str, surface_texts: list[str]) -> bool:
    """Return True if ``variable`` (as a whole-word token) appears in any surface text.

    Deliberately loose: any word-boundary mention counts as wired. This biases
    toward PASS to avoid false positives (blocking a legitimately wired PR).
    """
    pattern = re.compile(r"\b" + re.escape(variable) + r"\b")
    return any(pattern.search(text) for text in surface_texts)


# ---------------------------------------------------------------------------
# Core evaluation (pure -- fully unit-testable with in-memory inputs)
# ---------------------------------------------------------------------------


def evaluate(
    *,
    changed_variables_files: dict[str, tuple[str | None, str]],
    module_roots: list[str],
    terragrunt_files: dict[str, str],
) -> list[Finding]:
    """Evaluate the wiring guard against fully in-memory inputs.

    Args:
        changed_variables_files: Map of changed file path -> (base_text_or_None,
            head_text). Non-module-level ``variables.tf`` entries are ignored.
        module_roots: Module root prefixes from monorepo-config.json.
        terragrunt_files: Map of every terragrunt ``*.hcl`` path -> HEAD content.

    Returns:
        Sorted list of Findings (empty == PASS).
    """
    envcommon_by_name: dict[str, str] = {
        Path(path).name: text
        for path, text in terragrunt_files.items()
        if _ENVCOMMON_SEGMENT in path.replace("\\", "/")
        or path.replace("\\", "/").startswith("_envcommon/")
    }

    findings: list[Finding] = []

    for file_path, (base_text, head_text) in changed_variables_files.items():
        module_leaf = module_leaf_for_variables_file(file_path, module_roots)
        if module_leaf is None:
            continue

        new_vars = new_required_variables(base_text, head_text)
        if not new_vars:
            continue

        consuming_leaves = sorted(
            path
            for path, text in terragrunt_files.items()
            if _TERRAGRUNT_LIVE_SEGMENT in path.replace("\\", "/")
            and leaf_sources_module(text, module_leaf)
        )
        if not consuming_leaves:
            # No terragrunt leaf sources this module -> nothing to wire (PASS).
            continue

        for variable in new_vars:
            unwired: list[str] = []
            for leaf_path in consuming_leaves:
                leaf_text = terragrunt_files[leaf_path]
                surface = [leaf_text]
                for include_name in envcommon_includes(leaf_text):
                    if include_name in envcommon_by_name:
                        surface.append(envcommon_by_name[include_name])
                if not variable_is_wired(variable, surface):
                    unwired.append(leaf_path)
            if unwired:
                findings.append(
                    Finding(
                        module=module_leaf,
                        variable=variable,
                        unwired_leaves=tuple(sorted(unwired)),
                    )
                )

    return sorted(findings, key=lambda f: (f.module, f.variable))


# ---------------------------------------------------------------------------
# Git / filesystem integration
# ---------------------------------------------------------------------------


def _run_git(args: list[str], repo_root: str) -> subprocess.CompletedProcess[str]:
    """Run a git command rooted at ``repo_root`` and return the completed process."""
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True,
        text=True,
    )


def _changed_files(diff_range: str, repo_root: str) -> list[str]:
    """Return repo-relative changed files for a git diff range (fail-closed)."""
    completed = _run_git(["diff", "--name-only", diff_range], repo_root)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{GH_ERROR_PREFIX}git diff --name-only {diff_range!r} failed "
            f"(exit {completed.returncode}): {completed.stderr.strip()}. "
            "Ensure the diff range is valid and origin/main is fetched (fetch-depth: 0)."
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _base_ref_for_diff_range(diff_range: str, repo_root: str) -> str:
    """Resolve the base ref whose blobs represent the 'before' state of the range.

    For a three-dot ``A...B`` range, the base is ``git merge-base A B`` (the same
    point git diff treats as the base). For a two-dot ``A..B`` range, the base is
    ``A``. A bare ref is treated as the base directly.
    """
    if "..." in diff_range:
        left, _, right = diff_range.partition("...")
        left = left or "HEAD"
        right = right or "HEAD"
        completed = _run_git(["merge-base", left, right], repo_root)
        if completed.returncode != 0:
            raise RuntimeError(
                f"{GH_ERROR_PREFIX}git merge-base {left} {right} failed "
                f"(exit {completed.returncode}): {completed.stderr.strip()}."
            )
        return completed.stdout.strip()
    if ".." in diff_range:
        left, _, _ = diff_range.partition("..")
        return left or "HEAD"
    return diff_range


def _file_text_at_ref(path: str, base_ref: str, repo_root: str) -> str | None:
    """Return the blob content of ``path`` at ``base_ref``, or None if absent."""
    exists = _run_git(["cat-file", "-e", f"{base_ref}:{path}"], repo_root)
    if exists.returncode != 0:
        return None
    completed = _run_git(["show", f"{base_ref}:{path}"], repo_root)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{GH_ERROR_PREFIX}git show {base_ref}:{path} failed "
            f"(exit {completed.returncode}): {completed.stderr.strip()}."
        )
    return completed.stdout


def _collect_terragrunt_files(repo_root: str, terragrunt_root: str) -> dict[str, str]:
    """Read every terragrunt ``*.hcl`` file (repo-relative path -> HEAD content)."""
    root_path = Path(repo_root) / terragrunt_root.rstrip("/")
    files: dict[str, str] = {}
    if not root_path.exists():
        return files
    base = Path(repo_root)
    for hcl_path in sorted(root_path.rglob("*.hcl")):
        rel = hcl_path.relative_to(base).as_posix()
        files[rel] = hcl_path.read_text(encoding="utf-8")
    return files


def build_changed_variables_files(
    changed_files: list[str],
    module_roots: list[str],
    base_ref: str,
    repo_root: str,
) -> dict[str, tuple[str | None, str]]:
    """Build the {path: (base_text|None, head_text)} map for changed module variables.tf.

    Only module-level ``variables.tf`` files (per ``module_leaf_for_variables_file``)
    are included; deleted files (absent from the working tree) are skipped.
    """
    result: dict[str, tuple[str | None, str]] = {}
    for path in changed_files:
        if module_leaf_for_variables_file(path, module_roots) is None:
            continue
        head_path = Path(repo_root) / path
        if not head_path.is_file():
            # File deleted in HEAD -- a deletion cannot add a required variable.
            continue
        head_text = head_path.read_text(encoding="utf-8")
        base_text = _file_text_at_ref(path, base_ref, repo_root)
        result[path] = (base_text, head_text)
    return result


def run_guard(
    *,
    config: dict[str, object],
    diff_range: str,
    repo_root: str,
) -> list[Finding]:
    """Resolve inputs from git/filesystem and evaluate the guard."""
    module_roots, terragrunt_root = parse_scope_config(config)
    changed_files = _changed_files(diff_range, repo_root)
    base_ref = _base_ref_for_diff_range(diff_range, repo_root)
    changed_variables_files = build_changed_variables_files(
        changed_files=changed_files,
        module_roots=module_roots,
        base_ref=base_ref,
        repo_root=repo_root,
    )
    terragrunt_files = _collect_terragrunt_files(repo_root, terragrunt_root)
    return evaluate(
        changed_variables_files=changed_variables_files,
        module_roots=module_roots,
        terragrunt_files=terragrunt_files,
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Guard against a module required-variable added without terragrunt "
            "leaf wiring (issue #235)."
        )
    )
    parser.add_argument("--config", required=True, help="Path to monorepo-config.json")
    parser.add_argument(
        "--diff-range",
        default="origin/main...HEAD",
        help="git diff range whose --name-only changed files drive the check.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root for filesystem reads and git operations.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    try:
        findings = run_guard(
            config=config,
            diff_range=args.diff_range,
            repo_root=args.repo_root,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if findings:
        for finding in findings:
            print(finding.message(), file=sys.stderr)
        print(
            f"{GH_ERROR_PREFIX}Found {len(findings)} unwired new required module "
            "variable(s). Wire them into the consuming terragrunt leaves in this PR "
            "(or add a default) so `terragrunt plan` does not break post-merge.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("check_required_var_wiring: OK -- no unwired new required module variables detected.")
    sys.exit(0)


if __name__ == "__main__":
    main()

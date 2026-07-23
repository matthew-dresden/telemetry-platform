"""tg_no_hardcoded_identity -- anti-hardcode lint for the Terragrunt live tree.

Run via: uv run python -m scripts.tg_no_hardcoded_identity --root terragrunt/live

Walks all HCL files under --root and fails fast on any forbidden identity literal:
  - 12-digit AWS account id as a standalone string value
  - Region string ("us-east-1" or "useast1") as a quoted value
  - Domain apex substring (e.g. "telemetry.example.com")
  - CIDR literal matching 10.N.0.0/16 pattern
  - Digit-only path segment inside a dependency config_path value
  - Any local variable whose name starts with "declared_"

Allowlist (zero findings, always):
  - terragrunt/common/** (out-of-copy mapping layer; spec section 4.1)
  - **/terraform.tfvars (per-leaf deployment-unique values; spec section 4.5)

Config-driven exemptions for the current live tree:
  - exempt_account_id_var_names: variable names whose string assignments are exempt
    from the account-id rule. Used for state-bootstrap patterns (sandbox_account_id,
    prod_account_id) that are awaiting a dedicated refactor task.
  - exempt_config_path_patterns: regex patterns; a config_path whose value matches
    any of these is exempt from the digit-index rule. Used for:
      * Cross-account dependency paths (five-dotdot paths with a 12-digit account
        segment, spec-mandated by E7).
      * Cross-layer bootstrap dependency paths (paths traversing into bootstrap/).
  - mock_outputs blocks: literals inside mock_outputs = { ... } blocks are never
    flagged (these are offline plan/validate dummy values, not deployment config).

All forbidden-pattern definitions and allowlist paths are parameters/configuration;
nothing is inlined as magic strings in the scanning logic.

Error semantics (spec section 4.8):
  Any finding -> non-zero exit listing file:line + offending literal + remediation.
  Missing/unreadable live root -> ValueError raised, non-zero exit.

Environment variable consumed (optional):
  TG_LIVE_ROOT  -- override for the live tree root (default: derived from repo root).
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A single forbidden-literal finding."""

    file_path: Path
    line_number: int
    rule: str
    literal: str
    remediation: str


class HardcodedIdentityError(RuntimeError):
    """Raised when forbidden identity literals are found and raise_on_findings=True."""


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScannerConfig:
    """All scanner parameters. No inline constants in scanning logic.

    Attributes:
        live_root: Root path of the Terragrunt live tree to scan.
        allowlist_patterns: Glob patterns (relative to live_root.parent) for files
            that are never flagged. Typically ["common/**", "**/terraform.tfvars"].
        exempt_account_id_var_names: Set of variable names whose string assignments
            are exempt from the 12-digit account-id rule. Documents state-bootstrap
            patterns awaiting a dedicated refactor task.
        exempt_config_path_patterns: List of regex pattern strings. A config_path
            value matching any of these is exempt from the digit-index rule. Covers
            cross-account (E7) and cross-layer (bootstrap) paths that have legitimate
            fixed digit segments.
    """

    live_root: Path
    allowlist_patterns: Sequence[str]
    exempt_account_id_var_names: frozenset[str]
    exempt_config_path_patterns: Sequence[str]


# ---------------------------------------------------------------------------
# Compiled pattern constants (frozen; no magic values in scanning logic)
# ---------------------------------------------------------------------------

# Matches the start of a mock_outputs block (both assignment and bare block forms).
_MOCK_BLOCK_START_RE = re.compile(
    r"\bmock_outputs\s*=\s*\{"
    r"|\bmock_outputs\s*\{"
)

# Rule: 12-digit AWS account id as a standalone quoted value.
# Matches exactly 12 consecutive digits surrounded by word boundaries inside quotes.
_ACCOUNT_ID_IN_QUOTES_RE = re.compile(r'"(\d{12})"')

# Rule: Region string as a quoted value.
# Matches "us-east-1" or "useast1" (both dashed and collapsed forms).
_REGION_LITERAL_RE = re.compile(r'"(us-east-1|useast1)"')

# Rule: Domain apex substring.
# Matches your DNS apex so it can never be hardcoded in the live tree -- domain
# literals belong in common/domains.json (keyed by env-class), not in unit HCL.
# When you fork, set this to your own apex base (the value you put in
# domains.json, e.g. "telemetry.example.com" -> "example\.com" or your registered
# domain). The shipped default matches the example apex "telemetry.example.com".
_DOMAIN_APEX_RE = re.compile(r"telemetry\.example\.com")

# Rule: CIDR literal matching 10.N.0.0/16.
# Must appear as a quoted value.
_CIDR_LITERAL_RE = re.compile(r'"10\.\d+\.0\.0/16"')

# Captures the value of a config_path assignment.
_CONFIG_PATH_VALUE_RE = re.compile(r'\bconfig_path\s*=\s*"([^"]*)"')

# Matches a digit-only path segment (1 to 11 digits, excluding 12-digit account IDs).
# A segment is anything between two slashes or end of path.
_DIGIT_PATH_SEGMENT_RE = re.compile(r"/(\d{1,11})(?:/|$)")

# Rule: local variable declared with a name starting with "declared_".
_DECLARED_LOCAL_RE = re.compile(r"\bdeclared_\w+\s*=")

# Extracts the variable name from an assignment line: "  var_name = ..."
_ASSIGNMENT_VAR_NAME_RE = re.compile(r"^\s*(\w+)\s*=")

# ---------------------------------------------------------------------------
# Remediation messages (parameterised; per rule)
# ---------------------------------------------------------------------------

_REMEDIATION = {
    "account_id": (
        "Move the account id to common/accounts.json (keyed by account id) "
        "and derive it via basename(get_terragrunt_dir()) in account.hcl."
    ),
    "region": (
        "Derive the region from the directory basename via "
        "basename(get_terragrunt_dir()) in region.hcl; never hardcode a region string."
    ),
    "domain_apex": (
        "Move the domain apex to common/domains.json (keyed by env-class) "
        "and read it through include.root.locals or the common resolver."
    ),
    "cidr": (
        "Move the CIDR block to common/networks.json (keyed by namespace) "
        "and read it through the common resolver."
    ),
    "digit_config_path": (
        "Replace the hardcoded digit index with ${local.svc_instance} "
        "(derived from basename(get_terragrunt_dir()) in service_instance.hcl) "
        "so copied service instances resolve same-tier dependencies at the correct index."
    ),
    "declared_local": (
        "Remove the declared_* identity guard. Derive the value from "
        "basename(get_terragrunt_dir()) and validate via common/accounts.json lookup "
        "instead of an inline comparison."
    ),
}


# ---------------------------------------------------------------------------
# File-walking helper
# ---------------------------------------------------------------------------


def _collect_hcl_files(config: ScannerConfig) -> list[Path]:
    """Collect all HCL files under live_root, excluding allowlisted paths.

    Args:
        config: Scanner configuration.

    Returns:
        Sorted list of HCL file paths to scan.

    Raises:
        ValueError: If live_root does not exist or is not a directory.
    """
    if not config.live_root.exists():
        raise ValueError(
            f"ERROR: Live root '{config.live_root}' does not exist.\n"
            f"  Set --root to the path of the Terragrunt live tree or set TG_LIVE_ROOT."
        )
    if not config.live_root.is_dir():
        raise ValueError(
            f"ERROR: Live root '{config.live_root}' is not a directory.\n"
            f"  Provide the directory that contains HCL files to scan."
        )

    all_files: list[Path] = sorted(config.live_root.rglob("*.hcl"))
    parent = config.live_root.parent

    result: list[Path] = []
    for f in all_files:
        try:
            rel = str(f.relative_to(parent))
        except ValueError:
            rel = str(f)

        if _is_allowlisted(rel, config.allowlist_patterns):
            continue
        result.append(f)

    return result


def _is_allowlisted(rel_path: str, patterns: Sequence[str]) -> bool:
    """Return True if rel_path matches any allowlist glob pattern.

    Args:
        rel_path: Path relative to live_root.parent (POSIX separators).
        patterns: Iterable of glob patterns.

    Returns:
        True if the path should be skipped.
    """
    posix = rel_path.replace("\\", "/")
    for pattern in patterns:
        if fnmatch.fnmatch(posix, pattern):
            return True
        # Match on basename alone for patterns like "**/terraform.tfvars"
        basename = posix.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(basename, pattern.rsplit("/", 1)[-1]):
            # Re-check with the full pattern to avoid false positives on basename-only match
            pass
        # Support "common/**" by checking if any path component matches
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if posix.startswith(prefix + "/") or posix == prefix:
                return True
    return False


# ---------------------------------------------------------------------------
# Single-file scanning logic
# ---------------------------------------------------------------------------


def _scan_file(hcl_file: Path, config: ScannerConfig) -> list[Finding]:
    """Scan a single HCL file for forbidden identity literals.

    Skips:
    - Comment-only lines (stripped line starts with '#').
    - Content inside mock_outputs = { ... } blocks.
    - Inline comment portion of each line (text after the first unquoted '#').

    Args:
        hcl_file: Path to the HCL file.
        config: Scanner configuration.

    Returns:
        List of findings (may be empty).

    Raises:
        OSError: If the file cannot be read.
    """
    content = hcl_file.read_text(encoding="utf-8")
    lines = content.splitlines()

    findings: list[Finding] = []
    in_mock_block = False
    mock_brace_depth = 0

    for lineno, line in enumerate(lines, 1):
        stripped = line.lstrip()

        # Track entry into mock_outputs blocks
        if _MOCK_BLOCK_START_RE.search(line):
            in_mock_block = True
            mock_brace_depth = 0

        if in_mock_block:
            mock_brace_depth += line.count("{") - line.count("}")
            if mock_brace_depth <= 0:
                in_mock_block = False
            continue

        # Skip pure comment lines
        if stripped.startswith("#"):
            continue

        # Strip inline comment: take only the code portion
        code_part = _strip_inline_comment(line)

        # Apply all rules
        findings.extend(_check_account_id(hcl_file, lineno, code_part, config))
        findings.extend(_check_region(hcl_file, lineno, code_part))
        findings.extend(_check_domain_apex(hcl_file, lineno, code_part))
        findings.extend(_check_cidr(hcl_file, lineno, code_part))
        findings.extend(_check_config_path_digit_index(hcl_file, lineno, code_part, config))
        findings.extend(_check_declared_local(hcl_file, lineno, code_part))

    return findings


def _strip_inline_comment(line: str) -> str:
    """Return the code portion of a line by discarding text after an unquoted '#'.

    A '#' inside a quoted string is NOT a comment start. This simple implementation
    handles the common case by splitting at '#' and taking the first part, which is
    sufficient for HCL files where '#' inside string values is uncommon.

    Args:
        line: Raw line from an HCL file.

    Returns:
        Code portion of the line (may include trailing whitespace).
    """
    return line.split("#")[0]


def _check_account_id(
    hcl_file: Path,
    lineno: int,
    code_part: str,
    config: ScannerConfig,
) -> list[Finding]:
    """Check for a 12-digit account id as a standalone quoted value.

    Exempt:
    - Lines where the LHS variable name is in config.exempt_account_id_var_names.
    - Lines where the account id appears inside a config_path value.

    Args:
        hcl_file: File being scanned.
        lineno: 1-based line number.
        code_part: Code portion of the line (comments stripped).
        config: Scanner configuration.

    Returns:
        List of findings (0 or 1 per line).
    """
    if not _ACCOUNT_ID_IN_QUOTES_RE.search(code_part):
        return []

    # Exempt if this is a config_path assignment (cross-account dependency)
    if _CONFIG_PATH_VALUE_RE.search(code_part):
        return []

    # Exempt if the LHS variable name is in the exempt set
    m = _ASSIGNMENT_VAR_NAME_RE.match(code_part)
    if m and m.group(1) in config.exempt_account_id_var_names:
        return []

    match = _ACCOUNT_ID_IN_QUOTES_RE.search(code_part)
    if match is None:
        return []

    return [
        Finding(
            file_path=hcl_file,
            line_number=lineno,
            rule="account_id",
            literal=match.group(1),
            remediation=_REMEDIATION["account_id"],
        )
    ]


def _check_region(
    hcl_file: Path,
    lineno: int,
    code_part: str,
) -> list[Finding]:
    """Check for a region string literal ("us-east-1" or "useast1") as a quoted value.

    Args:
        hcl_file: File being scanned.
        lineno: 1-based line number.
        code_part: Code portion of the line.

    Returns:
        List of findings (0 or 1 per line).
    """
    match = _REGION_LITERAL_RE.search(code_part)
    if not match:
        return []

    return [
        Finding(
            file_path=hcl_file,
            line_number=lineno,
            rule="region",
            literal=match.group(1),
            remediation=_REMEDIATION["region"],
        )
    ]


def _check_domain_apex(
    hcl_file: Path,
    lineno: int,
    code_part: str,
) -> list[Finding]:
    """Check for a domain apex substring in the code portion.

    Args:
        hcl_file: File being scanned.
        lineno: 1-based line number.
        code_part: Code portion of the line.

    Returns:
        List of findings (0 or 1 per line).
    """
    match = _DOMAIN_APEX_RE.search(code_part)
    if not match:
        return []

    return [
        Finding(
            file_path=hcl_file,
            line_number=lineno,
            rule="domain_apex",
            literal=match.group(0),
            remediation=_REMEDIATION["domain_apex"],
        )
    ]


def _check_cidr(
    hcl_file: Path,
    lineno: int,
    code_part: str,
) -> list[Finding]:
    """Check for a CIDR literal ("10.N.0.0/16") as a quoted value.

    Args:
        hcl_file: File being scanned.
        lineno: 1-based line number.
        code_part: Code portion of the line.

    Returns:
        List of findings (0 or 1 per line).
    """
    match = _CIDR_LITERAL_RE.search(code_part)
    if not match:
        return []

    return [
        Finding(
            file_path=hcl_file,
            line_number=lineno,
            rule="cidr",
            literal=match.group(0),
            remediation=_REMEDIATION["cidr"],
        )
    ]


def _check_config_path_digit_index(
    hcl_file: Path,
    lineno: int,
    code_part: str,
    config: ScannerConfig,
) -> list[Finding]:
    """Check for a digit-only path segment inside a config_path value.

    Exempt paths:
    - Any config_path whose value matches a pattern in
      config.exempt_config_path_patterns (covers cross-account E7 paths and
      cross-layer bootstrap paths).

    Args:
        hcl_file: File being scanned.
        lineno: 1-based line number.
        code_part: Code portion of the line.
        config: Scanner configuration.

    Returns:
        List of findings (0 or 1 per line).
    """
    path_match = _CONFIG_PATH_VALUE_RE.search(code_part)
    if not path_match:
        return []

    path_value = path_match.group(1)

    # Check all exemption patterns
    for exempt_pattern in config.exempt_config_path_patterns:
        if re.search(exempt_pattern, path_value):
            return []

    # Look for digit-only path segments (1-11 digits, not 12-digit account IDs)
    seg_match = _DIGIT_PATH_SEGMENT_RE.search(path_value)
    if not seg_match:
        return []

    digit_seg = seg_match.group(1)
    return [
        Finding(
            file_path=hcl_file,
            line_number=lineno,
            rule="digit_config_path",
            literal=f"/{digit_seg}",
            remediation=_REMEDIATION["digit_config_path"],
        )
    ]


def _check_declared_local(
    hcl_file: Path,
    lineno: int,
    code_part: str,
) -> list[Finding]:
    """Check for a local variable whose name starts with "declared_".

    Args:
        hcl_file: File being scanned.
        lineno: 1-based line number.
        code_part: Code portion of the line.

    Returns:
        List of findings (0 or 1 per line).
    """
    match = _DECLARED_LOCAL_RE.search(code_part)
    if not match:
        return []

    return [
        Finding(
            file_path=hcl_file,
            line_number=lineno,
            rule="declared_local",
            literal=match.group(0).rstrip("= ").strip(),
            remediation=_REMEDIATION["declared_local"],
        )
    ]


# ---------------------------------------------------------------------------
# Public tree-scanning API
# ---------------------------------------------------------------------------


def scan_tree(
    config: ScannerConfig,
    raise_on_findings: bool = False,
) -> list[Finding]:
    """Scan the entire live tree for forbidden identity literals.

    Args:
        config: Scanner configuration.
        raise_on_findings: If True, raise HardcodedIdentityError when findings exist.

    Returns:
        List of all findings across all scanned files (empty on a clean tree).

    Raises:
        ValueError: If live_root does not exist or is not a directory.
        HardcodedIdentityError: If raise_on_findings=True and any findings exist.
        OSError: If any HCL file cannot be read.
    """
    hcl_files = _collect_hcl_files(config)

    all_findings: list[Finding] = []
    for hcl_file in hcl_files:
        file_findings = _scan_file(hcl_file, config)
        all_findings.extend(file_findings)

    if raise_on_findings and all_findings:
        summary = _format_findings(all_findings)
        raise HardcodedIdentityError(
            f"ERROR: {len(all_findings)} forbidden identity literal(s) found:\n{summary}"
        )

    return all_findings


def _format_findings(findings: list[Finding]) -> str:
    """Format findings as a human-readable string for error messages.

    Each finding is rendered as:
        <file>:<line>: [<rule>] '<literal>'
          Remediation: <remediation>

    Args:
        findings: Non-empty list of findings.

    Returns:
        Formatted multi-line string.
    """
    lines: list[str] = []
    for f in findings:
        lines.append(f"  {f.file_path}:{f.line_number}: [{f.rule}] '{f.literal}'")
        lines.append(f"    Remediation: {f.remediation}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public runner (used by Makefile target and CLI)
# ---------------------------------------------------------------------------


def run_scanner(config: ScannerConfig) -> int:
    """Run the scanner and return an exit code.

    On findings, prints ERROR-shaped messages to stderr.
    On a clean tree, prints a PASSED message to stdout.

    Args:
        config: Scanner configuration.

    Returns:
        0 on a clean tree; 1 on any finding or error.
    """
    try:
        findings = scan_tree(config)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if findings:
        print(
            f"ERROR: tg-no-hardcoded-identity FAILED: "
            f"{len(findings)} forbidden identity literal(s) found.",
            file=sys.stderr,
        )
        print(_format_findings(findings), file=sys.stderr)
        print(
            "\nRemediation: move each flagged value to common/ (scope-shared) "
            "or terraform.tfvars (deployment-unique) and derive identity values "
            "from the directory basename.",
            file=sys.stderr,
        )
        return 1

    print(
        f"tg-no-hardcoded-identity PASSED: "
        f"no forbidden identity literals found in {config.live_root}."
    )
    return 0


# ---------------------------------------------------------------------------
# Default config builder
# ---------------------------------------------------------------------------


def build_default_config(live_root: Path) -> ScannerConfig:
    """Build the default ScannerConfig for the production live tree.

    Reads pattern exemptions from module-level constants so the defaults
    remain configuration-driven and are easy to extend.

    Exemptions documented here:
    - exempt_account_id_var_names: ["sandbox_account_id", "prod_account_id"]
        State-bootstrap files (222222222222/bootstrap and 111111111111/bootstrap)
        use these variable names to name the state bucket. They await a dedicated
        refactor task to replace them with basename-derived values.
    - exempt_config_path_patterns: [r"/\\d{12}/", r"/bootstrap/"]
        Cross-account dependency paths (five-dotdot paths with a 12-digit account
        id segment, spec-mandated by E7) and cross-layer bootstrap dependency paths
        (paths traversing into a bootstrap/ environment subtree, awaiting E8-F4-S1-T2)
        contain legitimate fixed numeric segments.

    Args:
        live_root: Root path of the Terragrunt live tree.

    Returns:
        Configured ScannerConfig.
    """
    return ScannerConfig(
        live_root=live_root,
        allowlist_patterns=[
            "common/**",
            "**/terraform.tfvars",
        ],
        exempt_account_id_var_names=frozenset(
            [
                # State-bootstrap files use these variable names to name the state bucket.
                # They are a known pre-existing pattern awaiting a separate refactor task.
                "sandbox_account_id",
                "prod_account_id",
            ]
        ),
        exempt_config_path_patterns=[
            # Cross-account dependency paths (E7-spec-mandated, five-dotdot paths).
            # Example: config_path = "../../../../../111111111111/prod/000/dns-prod-zone/000"
            r"/\d{12}/",
            # Cross-layer bootstrap dependency paths (awaiting E8-F4-S1-T2).
            # Example: config_path = "../../../../../bootstrap/000/state-bootstrap/000"
            r"/bootstrap/",
        ],
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed namespace with .root attribute.
    """
    parser = argparse.ArgumentParser(
        prog="tg_no_hardcoded_identity",
        description=(
            "Scan terragrunt/live/** for forbidden identity literals. "
            "Exits non-zero with file:line findings when any are detected."
        ),
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        default=None,
        help=(
            "Path to the Terragrunt live tree root to scan. "
            "Defaults to TG_LIVE_ROOT env var or <repo-root>/terragrunt/live."
        ),
    )
    return parser.parse_args(argv)


def _resolve_live_root(root_arg: str | None) -> Path:
    """Resolve the live tree root from CLI arg or environment.

    Args:
        root_arg: Value passed via --root (may be None).

    Returns:
        Resolved Path to the live tree root.

    Raises:
        ValueError: If no root can be determined.
    """
    import os

    if root_arg:
        return Path(root_arg)

    env_root = os.environ.get("TG_LIVE_ROOT", "")
    if env_root:
        return Path(env_root)

    repo_root = Path(__file__).parent.parent
    candidate = repo_root / "terragrunt" / "live"
    if candidate.exists():
        return candidate

    raise ValueError(
        "ERROR: Cannot determine live tree root.\n"
        "  Pass --root <path> or set the TG_LIVE_ROOT environment variable."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `uv run python -m scripts.tg_no_hardcoded_identity`.

    Args:
        argv: Optional argument list override (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = clean, 1 = findings or error).
    """
    args = _parse_args(argv)

    try:
        live_root = _resolve_live_root(args.root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = build_default_config(live_root)
    return run_scanner(config)


if __name__ == "__main__":
    sys.exit(main())

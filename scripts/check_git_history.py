"""check_git_history -- FR-6 git history blob-size checker and evidence verifier.

Run via:
  uv run python -m scripts.check_git_history [--max-blob-bytes N | --verify-tree-evidence FILE]

Provides two operating modes:

  --max-blob-bytes <n>
      Walk every blob reachable from all branches using `git rev-list --objects
      --branches` piped into `git cat-file --batch-check`. Print one `<bytes> <path>`
      line per blob that exceeds the threshold to stdout, and exit 1. Exit 0 when
      no blob exceeds the threshold.

  --verify-tree-evidence <file>
      Load docs/evidence/history-rewrite.json (spec section 5.3) and assert
      the identity invariant:
          pre_tip_tree == post_tip_tree
      Exit 0 when the invariant holds; exit 1 naming the mismatching field.
      Raise EvidenceVerifyError (exit non-zero) on a missing file, malformed JSON,
      or missing required key.

Architecture (SRP):
  - walk_blobs():            yields BlobOffender for every blob across all refs
  - check_max_blob_bytes():  applies the threshold and reports; returns exit code
  - verify_tree_evidence():  loads evidence and asserts the invariant; returns exit code
  - main():                  CLI dispatcher; sys.exit is ONLY called here

Environment variables consumed:
  None -- all configuration is via CLI arguments.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlobOffender:
    """A git blob that exceeds the configured size threshold.

    Attributes:
        size_bytes: Byte size of the blob object.
        path:       File path reported by git cat-file (may be the object SHA
                    when a path annotation is unavailable for historical objects).
    """

    size_bytes: int
    path: str


class EvidenceVerifyError(RuntimeError):
    """Raised when the evidence file cannot be loaded or is structurally invalid."""


# ---------------------------------------------------------------------------
# Required evidence schema fields (spec section 5.3)
# ---------------------------------------------------------------------------

_REQUIRED_EVIDENCE_KEYS = (
    "pre_tip_commit",
    "pre_tip_tree",
    "post_tip_commit",
    "post_tip_tree",
)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def walk_blobs(cwd: str | None = None) -> list[BlobOffender]:
    """Walk every blob object reachable from all branches.

    Uses a two-step approach:
    1. `git rev-list --objects --branches` to enumerate every reachable object with
       its file path. Only objects reachable from at least one branch ref are included;
       tags and unreachable (dangling) objects are excluded. This aligns with the
       spec AC-1 requirement: "no blob anywhere in the branch history exceeds
       100,000,000 bytes" (spec section 4.6). Tags (e.g. pre-rewrite recovery tags)
       are intentionally excluded so the check reflects the live branch history only.
    2. `git cat-file --batch-check` (via stdin) to resolve the type and byte size
       of each object; blob-type objects produce a BlobOffender entry.

    Args:
        cwd: Working directory for git commands. None inherits the caller's cwd.

    Returns:
        List of BlobOffender, one per blob object reachable from any branch.

    Raises:
        subprocess.CalledProcessError: If git invocations fail.
    """
    # Step 1: enumerate every reachable object with its path annotation.
    # Output format per line: "<sha> [<path>]"  (path may be absent for commits/root-trees)
    revlist_proc = subprocess.run(
        ["git", "rev-list", "--objects", "--branches"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )

    # Build a sha -> path mapping and an ordered list of all SHAs for batch-check input.
    sha_to_path: dict[str, str] = {}
    sha_order: list[str] = []
    for line in revlist_proc.stdout.splitlines():
        if not line:
            continue
        idx = line.find(" ")
        if idx == -1:
            sha = line
            path = sha  # commit or root-tree: no path annotation; fall back to SHA
        else:
            sha = line[:idx]
            path = line[idx + 1 :].strip() or sha
        sha_to_path[sha] = path
        sha_order.append(sha)

    if not sha_order:
        return []

    # Step 2: query type and size for all reachable objects in one batch.
    # Feed SHAs via stdin; output: "<sha> <type> <size>" per object.
    batch_input = "\n".join(sha_order) + "\n"
    check_proc = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input=batch_input,
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )

    result: list[BlobOffender] = []
    for line in check_proc.stdout.splitlines():
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        obj_sha, obj_type, obj_size_str = parts[0], parts[1], parts[2]
        if obj_type != "blob":
            continue
        try:
            size = int(obj_size_str)
        except ValueError:
            continue
        path = sha_to_path.get(obj_sha, obj_sha)
        result.append(BlobOffender(size_bytes=size, path=path))

    return result


def check_max_blob_bytes(max_bytes: int, cwd: str | None = None) -> int:
    """Walk all blobs and report those exceeding max_bytes.

    Each offending blob is printed as:
        <size_bytes> <path>
    to stdout. After all offenders are printed, exits 1. Exits 0 when
    no blob exceeds the threshold.

    Args:
        max_bytes: Inclusive upper bound on blob size in bytes. Blobs with
                   size > max_bytes are offenders.
        cwd:       Working directory for git commands.

    Returns:
        0 if no offenders, 1 if any offenders found.
    """
    blobs = walk_blobs(cwd=cwd)
    offenders = [b for b in blobs if b.size_bytes > max_bytes]
    if not offenders:
        return 0
    for o in offenders:
        print(f"{o.size_bytes} {o.path}")
    return 1


def verify_tree_evidence(evidence_file: str) -> int:
    """Load history-rewrite evidence JSON and assert the identity invariant.

    The identity invariant (spec section 5.3):
        pre_tip_tree == post_tip_tree

    This proves the filter-repo rewrite did not alter the working tree content.

    Args:
        evidence_file: Path to docs/evidence/history-rewrite.json.

    Returns:
        0 if the invariant holds.
        1 if pre_tip_tree != post_tip_tree (prints the mismatch details to stderr).

    Raises:
        EvidenceVerifyError: If the file is missing, not valid JSON, or lacks
                             a required key.
    """
    epath = pathlib.Path(evidence_file)
    if not epath.exists():
        raise EvidenceVerifyError(
            f"ERROR: evidence file not found: {evidence_file}\n"
            f"  Ensure docs/evidence/history-rewrite.json has been committed."
        )

    try:
        data: dict[str, object] = json.loads(epath.read_text())
    except json.JSONDecodeError as exc:
        raise EvidenceVerifyError(
            f"ERROR: evidence file contains invalid JSON: {evidence_file}\n"
            f"  Json decode error: {exc}"
        ) from exc

    for key in _REQUIRED_EVIDENCE_KEYS:
        if key not in data:
            raise EvidenceVerifyError(
                f"ERROR: evidence file missing required key '{key}': {evidence_file}\n"
                f"  Add '{key}' to the evidence document per spec section 5.3."
            )

    pre_tree = data["pre_tip_tree"]
    post_tree = data["post_tip_tree"]

    if pre_tree != post_tree:
        print(
            f"ERROR: rewrite identity invariant violated:\n"
            f"  pre_tip_tree  = {pre_tree}\n"
            f"  post_tip_tree = {post_tree}\n"
            f"  pre_tip_tree must equal post_tip_tree (spec section 5.3 invariant).",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: pre_tip_tree == post_tip_tree == {pre_tree}\n"
        f"    history-rewrite identity invariant satisfied."
    )
    return 0


# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for check_git_history.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Integer exit code (0 = success, 1 = failure).
    """
    parser = argparse.ArgumentParser(
        prog="check_git_history",
        description=(
            "FR-6 git history checker: blob-size walk and evidence verification.\n"
            "\n"
            "Modes:\n"
            "  --max-blob-bytes N       Walk all blobs; exit 1 if any exceeds N bytes.\n"
            "  --verify-tree-evidence F Load evidence JSON; exit 1 if pre != post tree."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--max-blob-bytes",
        type=int,
        metavar="N",
        help="Maximum permitted blob size in bytes (inclusive).",
    )
    mode_group.add_argument(
        "--verify-tree-evidence",
        metavar="FILE",
        help="Path to docs/evidence/history-rewrite.json to verify.",
    )

    args = parser.parse_args(argv)

    if args.max_blob_bytes is not None:
        return check_max_blob_bytes(max_bytes=args.max_blob_bytes)

    # --verify-tree-evidence mode
    try:
        return verify_tree_evidence(evidence_file=args.verify_tree_evidence)
    except EvidenceVerifyError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

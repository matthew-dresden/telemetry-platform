"""Unit tests for scripts/check_git_history.py.

Covers FR-6 git history checker (spec section 4.6, AC #14, AC #15):

  - Blob walk over git rev-list --objects --branches piped to git cat-file --batch-check
  - --max-blob-bytes threshold: exit 0 when all blobs pass, exit 1 with
    offender lines when any blob exceeds the threshold
  - --verify-tree-evidence: loads evidence JSON, asserts pre_tip_tree ==
    post_tip_tree; exits 0 on match, exits 1 naming the mismatching field
  - Malformed evidence file paths (missing keys, invalid JSON, missing file)
  - main() exit codes via subprocess invocation and direct call
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from scripts.check_git_history import (
    BlobOffender,
    EvidenceVerifyError,
    check_max_blob_bytes,
    verify_tree_evidence,
    walk_blobs,
)

# ---------------------------------------------------------------------------
# Git repo fixture helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Initialise a bare-minimum git repo with one commit and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
        cwd=str(repo),
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        check=True,
        capture_output=True,
        cwd=str(repo),
    )
    # Initial commit so HEAD exists
    readme = repo / "README.md"
    readme.write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True, cwd=str(repo))
    subprocess.run(
        ["git", "commit", "-m", "init"],
        check=True,
        capture_output=True,
        cwd=str(repo),
    )
    return repo


def _add_blob(repo: pathlib.Path, filename: str, size_bytes: int) -> None:
    """Commit a file with the given byte size to the repo."""
    filepath = repo / filename
    filepath.write_bytes(b"x" * size_bytes)
    subprocess.run(["git", "add", filename], check=True, capture_output=True, cwd=str(repo))
    subprocess.run(
        ["git", "commit", "-m", f"add {filename}"],
        check=True,
        capture_output=True,
        cwd=str(repo),
    )


def _get_head_tree(repo: pathlib.Path) -> str:
    """Return the tree SHA of HEAD."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    return result.stdout.strip()


def _get_head_commit(repo: pathlib.Path) -> str:
    """Return the commit SHA of HEAD."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# BlobOffender dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_blob_offender_fields() -> None:
    """BlobOffender must expose size_bytes and path attributes."""
    o = BlobOffender(size_bytes=12345, path="some/path/file.bin")
    assert o.size_bytes == 12345
    assert o.path == "some/path/file.bin"


# ---------------------------------------------------------------------------
# walk_blobs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_walk_blobs_empty_repo(tmp_path: pathlib.Path) -> None:
    """walk_blobs on a repo with a branch containing only a small README yields one blob entry."""
    repo = _init_repo(tmp_path)
    blobs = list(walk_blobs(cwd=str(repo)))
    # README.md should produce at least one blob
    assert len(blobs) >= 1
    # All entries must be (size_int, path_str) pairs (BlobOffender-like)
    for b in blobs:
        assert isinstance(b.size_bytes, int)
        assert isinstance(b.path, str)
        assert b.size_bytes >= 0


@pytest.mark.unit
def test_walk_blobs_counts_all_blobs(tmp_path: pathlib.Path) -> None:
    """walk_blobs must return one entry per blob across all refs."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "small.bin", 100)
    _add_blob(repo, "big.bin", 500)
    blobs = list(walk_blobs(cwd=str(repo)))
    paths = [b.path for b in blobs]
    # The two added files must appear
    assert any("small.bin" in p for p in paths), f"small.bin not found in {paths}"
    assert any("big.bin" in p for p in paths), f"big.bin not found in {paths}"


@pytest.mark.unit
def test_walk_blobs_size_accuracy(tmp_path: pathlib.Path) -> None:
    """walk_blobs must report the correct size_bytes for each blob."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "exact.bin", 999)
    blobs = list(walk_blobs(cwd=str(repo)))
    matching = [b for b in blobs if "exact.bin" in b.path]
    assert matching, "exact.bin blob not found in walk_blobs output"
    assert matching[0].size_bytes == 999, f"Expected 999 bytes, got {matching[0].size_bytes}"


# ---------------------------------------------------------------------------
# check_max_blob_bytes -- pass path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_max_blob_bytes_all_pass(tmp_path: pathlib.Path) -> None:
    """check_max_blob_bytes exits 0 when no blob exceeds the threshold."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "small.bin", 50)
    result = check_max_blob_bytes(max_bytes=100, cwd=str(repo))
    assert result == 0, f"Expected exit 0 (all blobs pass threshold), got {result}"


@pytest.mark.unit
def test_check_max_blob_bytes_passes_when_exactly_at_limit(tmp_path: pathlib.Path) -> None:
    """check_max_blob_bytes exits 0 when blob size == threshold (not over)."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "exact.bin", 100)
    result = check_max_blob_bytes(max_bytes=100, cwd=str(repo))
    assert result == 0, "A blob exactly at the threshold must not be an offender."


# ---------------------------------------------------------------------------
# check_max_blob_bytes -- offender path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_max_blob_bytes_reports_offender(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """check_max_blob_bytes exits 1 and prints offender line for oversized blob."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "huge.bin", 200)
    result = check_max_blob_bytes(max_bytes=100, cwd=str(repo))
    assert result == 1, f"Expected exit 1 for oversized blob, got {result}"
    captured = capsys.readouterr()
    assert "huge.bin" in captured.out or "huge.bin" in captured.err, (
        "Offender path 'huge.bin' must appear in output"
    )
    assert "200" in captured.out or "200" in captured.err, (
        "Offender size '200' must appear in output"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "sizes, threshold, expected_exit",
    [
        ([50, 80], 100, 0),
        ([150, 80], 100, 1),
        ([150, 200], 100, 1),
        ([0, 100], 100, 0),
        ([0, 101], 100, 1),
    ],
)
def test_check_max_blob_bytes_parametrized(
    tmp_path: pathlib.Path,
    sizes: list[int],
    threshold: int,
    expected_exit: int,
) -> None:
    """check_max_blob_bytes parametrized over threshold/size combinations."""
    repo = _init_repo(tmp_path)
    for i, sz in enumerate(sizes):
        _add_blob(repo, f"file{i}.bin", sz)
    result = check_max_blob_bytes(max_bytes=threshold, cwd=str(repo))
    assert result == expected_exit, (
        f"sizes={sizes} threshold={threshold}: expected exit {expected_exit}, got {result}"
    )


# ---------------------------------------------------------------------------
# verify_tree_evidence -- success path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_verify_tree_evidence_success(tmp_path: pathlib.Path) -> None:
    """verify_tree_evidence exits 0 when pre_tip_tree == post_tip_tree."""
    # Use a synthetic tree SHA (any sha-like string -- we test the logic, not git)
    tree_sha = "deadbeef" * 5  # 40 chars
    evidence = {
        "rewritten_at": "2026-06-12T21:23:15Z",
        "pre_tip_commit": "a" * 40,
        "pre_tip_tree": tree_sha,
        "post_tip_commit": "b" * 40,
        "post_tip_tree": tree_sha,
        "filter_args": "--path-glob **/.terraform/** --invert-paths",
        "max_blob_bytes_after": 0,
        "force_with_lease_used": True,
    }
    evidence_path = tmp_path / "history-rewrite.json"
    evidence_path.write_text(json.dumps(evidence))

    result = verify_tree_evidence(evidence_file=str(evidence_path))
    assert result == 0, f"Expected exit 0 when pre_tip_tree == post_tip_tree, got {result}"


# ---------------------------------------------------------------------------
# verify_tree_evidence -- mismatch paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_verify_tree_evidence_pre_post_mismatch(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """verify_tree_evidence exits 1 and names the field when pre != post tree."""
    evidence = {
        "rewritten_at": "2026-06-12T21:23:15Z",
        "pre_tip_commit": "a" * 40,
        "pre_tip_tree": "1" * 40,
        "post_tip_commit": "b" * 40,
        "post_tip_tree": "2" * 40,
        "filter_args": "--path-glob **/.terraform/** --invert-paths",
        "max_blob_bytes_after": 0,
        "force_with_lease_used": True,
    }
    evidence_path = tmp_path / "history-rewrite.json"
    evidence_path.write_text(json.dumps(evidence))

    result = verify_tree_evidence(evidence_file=str(evidence_path))
    assert result == 1, f"Expected exit 1 for pre/post tree mismatch, got {result}"
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "pre_tip_tree" in combined or "post_tip_tree" in combined, (
        "Error output must name the mismatching field(s)"
    )


# ---------------------------------------------------------------------------
# verify_tree_evidence -- malformed evidence file
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_verify_tree_evidence_missing_file(tmp_path: pathlib.Path) -> None:
    """verify_tree_evidence raises EvidenceVerifyError when the file does not exist."""
    with pytest.raises(EvidenceVerifyError, match="not found"):
        verify_tree_evidence(evidence_file=str(tmp_path / "nonexistent.json"))


@pytest.mark.unit
def test_verify_tree_evidence_invalid_json(tmp_path: pathlib.Path) -> None:
    """verify_tree_evidence raises EvidenceVerifyError for malformed JSON."""
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{ not valid json }")
    with pytest.raises(EvidenceVerifyError, match="[Jj][Ss][Oo][Nn]|[Pp]arse|[Dd]ecode"):
        verify_tree_evidence(evidence_file=str(bad_path))


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_key",
    ["pre_tip_tree", "post_tip_tree", "pre_tip_commit", "post_tip_commit"],
)
def test_verify_tree_evidence_missing_key(tmp_path: pathlib.Path, missing_key: str) -> None:
    """verify_tree_evidence raises EvidenceVerifyError when a required key is absent."""
    evidence = {
        "rewritten_at": "2026-06-12T21:23:15Z",
        "pre_tip_commit": "a" * 40,
        "pre_tip_tree": "b" * 40,
        "post_tip_commit": "c" * 40,
        "post_tip_tree": "b" * 40,
        "filter_args": "--path-glob **/.terraform/** --invert-paths",
        "max_blob_bytes_after": 0,
        "force_with_lease_used": True,
    }
    del evidence[missing_key]
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))

    with pytest.raises(EvidenceVerifyError, match=missing_key):
        verify_tree_evidence(evidence_file=str(evidence_path))


# ---------------------------------------------------------------------------
# main() exit codes via subprocess
# ---------------------------------------------------------------------------


def _run_main(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke scripts.check_git_history as a module subprocess."""
    cmd = [sys.executable, "-m", "scripts.check_git_history", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


@pytest.mark.unit
def test_main_max_blob_bytes_exit_0_subprocess(tmp_path: pathlib.Path) -> None:
    """main() exits 0 via subprocess when no blob exceeds the threshold."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "tiny.bin", 10)
    proc = _run_main("--max-blob-bytes", "100000000", cwd=str(repo))
    assert proc.returncode == 0, f"Expected exit 0; stderr={proc.stderr!r}"


@pytest.mark.unit
def test_main_max_blob_bytes_exit_1_subprocess(tmp_path: pathlib.Path) -> None:
    """main() exits 1 via subprocess when a blob exceeds the threshold."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "oversized.bin", 200)
    proc = _run_main("--max-blob-bytes", "100", cwd=str(repo))
    assert proc.returncode == 1, f"Expected exit 1 for oversized blob; stderr={proc.stderr!r}"
    assert "oversized.bin" in proc.stdout or "oversized.bin" in proc.stderr, (
        "Offender path must appear in subprocess output"
    )


@pytest.mark.unit
def test_main_verify_tree_evidence_exit_0_subprocess(tmp_path: pathlib.Path) -> None:
    """main() exits 0 via subprocess when evidence shows pre == post tree."""
    tree_sha = "deadbeef" * 5
    evidence = {
        "rewritten_at": "2026-06-12T21:23:15Z",
        "pre_tip_commit": "a" * 40,
        "pre_tip_tree": tree_sha,
        "post_tip_commit": "b" * 40,
        "post_tip_tree": tree_sha,
        "filter_args": "--path-glob **/.terraform/** --invert-paths",
        "max_blob_bytes_after": 0,
        "force_with_lease_used": True,
    }
    evidence_path = tmp_path / "history-rewrite.json"
    evidence_path.write_text(json.dumps(evidence))
    proc = _run_main("--verify-tree-evidence", str(evidence_path))
    assert proc.returncode == 0, f"Expected exit 0 when trees match; stderr={proc.stderr!r}"


@pytest.mark.unit
def test_main_verify_tree_evidence_exit_1_subprocess(tmp_path: pathlib.Path) -> None:
    """main() exits 1 via subprocess when evidence shows pre != post tree."""
    evidence = {
        "rewritten_at": "2026-06-12T21:23:15Z",
        "pre_tip_commit": "a" * 40,
        "pre_tip_tree": "1" * 40,
        "post_tip_commit": "b" * 40,
        "post_tip_tree": "2" * 40,
        "filter_args": "--path-glob **/.terraform/** --invert-paths",
        "max_blob_bytes_after": 0,
        "force_with_lease_used": True,
    }
    evidence_path = tmp_path / "history-rewrite.json"
    evidence_path.write_text(json.dumps(evidence))
    proc = _run_main("--verify-tree-evidence", str(evidence_path))
    assert proc.returncode == 1, f"Expected exit 1 for tree mismatch; stderr={proc.stderr!r}"


@pytest.mark.unit
def test_main_no_args_exits_nonzero() -> None:
    """main() exits non-zero when called with no arguments."""
    proc = _run_main()
    assert proc.returncode != 0, "Expected non-zero exit when no subcommand is given."


@pytest.mark.unit
def test_main_missing_evidence_file_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """main() exits non-zero when evidence file does not exist."""
    missing = str(tmp_path / "nonexistent.json")
    proc = _run_main("--verify-tree-evidence", missing)
    assert proc.returncode != 0, (
        f"Expected non-zero exit for missing evidence file; stderr={proc.stderr!r}"
    )


# ---------------------------------------------------------------------------
# main() via direct call (import path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_direct_call_max_blob_bytes_clean(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 0 when called directly with a clean repo."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(str(repo))
    from scripts.check_git_history import main as ghm

    rc = ghm(["--max-blob-bytes", "100000000"])
    assert rc == 0, f"Expected return code 0 from direct main() call, got {rc}"


@pytest.mark.unit
def test_main_direct_call_max_blob_bytes_offender(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() returns 1 when called directly and a blob exceeds the threshold."""
    repo = _init_repo(tmp_path)
    _add_blob(repo, "bigfile.bin", 500)
    monkeypatch.chdir(str(repo))
    from scripts.check_git_history import main as ghm

    rc = ghm(["--max-blob-bytes", "100"])
    assert rc == 1, f"Expected return code 1 from direct main() call, got {rc}"


# ---------------------------------------------------------------------------
# walk_blobs edge cases (coverage for early-exit paths)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_walk_blobs_empty_revlist(tmp_path: pathlib.Path) -> None:
    """walk_blobs returns an empty list when rev-list returns no objects.

    Exercises the early-exit path when `git rev-list --objects --all` produces
    no output (e.g. an empty repo with no refs).
    """
    import subprocess as sp
    from unittest.mock import patch

    # Simulate an empty rev-list output (no reachable objects)
    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout="",
        stderr="",
    )
    with patch("subprocess.run", return_value=fake_revlist) as mock_run:
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    # Only one call should have been made (rev-list); cat-file is skipped
    assert result == [], f"Expected empty list for empty rev-list, got {result!r}"
    assert mock_run.call_count == 1, "walk_blobs should not call cat-file when rev-list is empty"


@pytest.mark.unit
def test_walk_blobs_no_blob_objects(tmp_path: pathlib.Path) -> None:
    """walk_blobs returns an empty list when cat-file finds no blob objects."""
    import subprocess as sp
    from unittest.mock import patch

    commit_sha = "1122334455667788990011223344556677aabbcc"
    tree_sha = "aabbccdd11223344556677889900112233445566"

    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{commit_sha}\n{tree_sha} \n",
        stderr="",
    )
    # cat-file responds with only commit and tree objects (no blobs)
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{commit_sha} commit 250\n{tree_sha} tree 40\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    assert result == [], f"Expected empty list for non-blob objects, got {result!r}"


@pytest.mark.unit
def test_walk_blobs_revlist_empty_line_skipped(tmp_path: pathlib.Path) -> None:
    """walk_blobs skips blank lines in rev-list output.

    Exercises the `if not line: continue` branch when the rev-list output
    contains an empty line (e.g. at the start or between entries).
    """
    import subprocess as sp
    from unittest.mock import patch

    blob_sha = "aabbccdd1122334455667788990011223344556677"
    commit_sha = "1122334455667788990011223344556677aabbcc"

    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        # Leading empty line exercises `if not line: continue`
        stdout=f"\n{commit_sha}\n{blob_sha} data/file.bin\n",
        stderr="",
    )
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{commit_sha} commit 100\n{blob_sha} blob 42\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    assert len(result) == 1, f"Expected 1 blob, got {result!r}"
    assert result[0].path == "data/file.bin"
    assert result[0].size_bytes == 42


@pytest.mark.unit
def test_walk_blobs_revlist_line_without_space(tmp_path: pathlib.Path) -> None:
    """walk_blobs handles rev-list output lines that have no path (SHA only).

    When git rev-list --objects --all emits a line with just a SHA and no space
    (e.g. a commit SHA), walk_blobs must use the SHA itself as the path fallback
    for that object.
    """
    import subprocess as sp
    from unittest.mock import patch

    blob_sha = "aabbccdd1122334455667788990011223344556677"
    # commit_sha has no space -- exercises the `idx == -1` branch
    commit_sha = "1122334455667788990011223344556677aabbcc"

    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        # commit_sha line has no space (no path annotation)
        stdout=f"{commit_sha}\n{blob_sha} some/nested/file.bin\n",
        stderr="",
    )
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{commit_sha} commit 300\n{blob_sha} blob 77\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    assert len(result) == 1, f"Expected 1 blob (commit filtered out), got {result!r}"
    assert result[0].path == "some/nested/file.bin"
    assert result[0].size_bytes == 77


@pytest.mark.unit
def test_walk_blobs_cat_file_empty_line_skipped(tmp_path: pathlib.Path) -> None:
    """walk_blobs skips empty lines in cat-file --batch-check output.

    Exercises the `if not line: continue` branch when the batch-check output
    contains an empty line between valid entries.
    """
    import subprocess as sp
    from unittest.mock import patch

    blob_sha = "aabbccdd1122334455667788990011223344556677"
    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{blob_sha} file.bin\n",
        stderr="",
    )
    # Empty line in the middle of batch-check output exercises the continue branch
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"\n{blob_sha} blob 55\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    assert len(result) == 1, f"Expected 1 blob after skipping empty line, got {result!r}"
    assert result[0].size_bytes == 55


@pytest.mark.unit
def test_walk_blobs_cat_file_malformed_line(tmp_path: pathlib.Path) -> None:
    """walk_blobs skips malformed cat-file lines (fewer than 3 tokens)."""
    import subprocess as sp
    from unittest.mock import patch

    blob_sha = "aabbccdd1122334455667788990011223344556677"
    good_sha = "bbccdd1122334455667788990011223344556677aa"

    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{blob_sha} bad/file.bin\n{good_sha} good/file.bin\n",
        stderr="",
    )
    # A malformed cat-file line (only 2 tokens) followed by a valid blob line
    # This exercises the `if len(parts) < 3: continue` branch
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{blob_sha} blob\n{good_sha} blob 200\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    # The malformed line is skipped; only the valid blob should be in the result
    assert len(result) == 1, f"Expected 1 blob from valid line, got {result!r}"
    assert result[0].size_bytes == 200


@pytest.mark.unit
def test_walk_blobs_non_integer_size_skipped(tmp_path: pathlib.Path) -> None:
    """walk_blobs skips blob objects whose size field is not a valid integer."""
    import subprocess as sp
    from unittest.mock import patch

    blob_sha = "aabbccdd1122334455667788990011223344556677"
    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{blob_sha} file.bin\n",
        stderr="",
    )
    # A blob with non-integer size triggers the ValueError branch
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{blob_sha} blob NOTANINT\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    assert result == [], f"Expected empty list after size parse failure, got {result!r}"


@pytest.mark.unit
def test_walk_blobs_blob_sha_not_in_revlist_falls_back_to_sha(tmp_path: pathlib.Path) -> None:
    """walk_blobs falls back to SHA as path when sha_to_path has no entry.

    Exercises the `sha_to_path.get(obj_sha, obj_sha)` fallback: when cat-file
    returns a blob SHA that wasn't found in the rev-list sha_to_path mapping
    (e.g. due to a race condition or unusual git state), the path falls back to
    the SHA string itself.
    """
    import subprocess as sp
    from unittest.mock import patch

    blob_sha = "aabbccdd1122334455667788990011223344556677"
    other_sha = "1122334455667788990011223344556677aabbcc"

    # rev-list only lists the other_sha -- blob_sha is not in sha_to_path
    fake_revlist = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{other_sha} other/file.txt\n",
        stderr="",
    )
    # cat-file returns blob_sha as a blob (it was in the input list via sha_order)
    # but sha_to_path won't have it, so path falls back to blob_sha itself.
    # NOTE: in practice sha_order only contains SHAs from revlist, so this is
    # a defensive test for the .get(sha, sha) fallback logic.
    fake_check = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{blob_sha} blob 99\n",
        stderr="",
    )
    results = [fake_revlist, fake_check]

    def fake_run(cmd, **kwargs):
        return results.pop(0)

    with patch("subprocess.run", side_effect=fake_run):
        from scripts import check_git_history as cgh

        result = cgh.walk_blobs(cwd=str(tmp_path))

    assert len(result) == 1, f"Expected 1 blob with SHA fallback, got {result!r}"
    # The path falls back to the SHA itself
    assert result[0].path == blob_sha, f"Expected path to fall back to SHA, got {result[0].path!r}"
    assert result[0].size_bytes == 99


# ---------------------------------------------------------------------------
# main() EvidenceVerifyError is caught and prints to stderr
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_direct_call_verify_evidence_error_returns_1(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when verify_tree_evidence raises EvidenceVerifyError."""
    missing = str(tmp_path / "missing.json")
    from scripts.check_git_history import main as ghm

    rc = ghm(["--verify-tree-evidence", missing])
    assert rc == 1, f"Expected return code 1 for missing evidence file, got {rc}"


# ---------------------------------------------------------------------------
# __main__ block coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_module_invocable_as_script(tmp_path: pathlib.Path) -> None:
    """scripts.check_git_history is runnable as a top-level module (__main__ block).

    Exercises the `if __name__ == '__main__': sys.exit(main())` block by running
    the module via runpy, which sets __name__ to '__main__' inside the in-process
    interpreter (allowing coverage to track the block without spawning a subprocess).
    """
    import runpy
    from unittest.mock import patch

    repo = _init_repo(tmp_path)

    with patch("sys.argv", ["check_git_history", "--max-blob-bytes", "100000000"]):
        # Change working directory to the repo so git commands run in the right place
        orig_dir = pathlib.Path.cwd()
        try:
            import os

            os.chdir(str(repo))
            with pytest.raises(SystemExit) as exc_info:
                runpy.run_module(
                    "scripts.check_git_history",
                    run_name="__main__",
                    alter_sys=True,
                )
        finally:
            os.chdir(str(orig_dir))

    assert exc_info.value.code == 0, (
        f"Expected sys.exit(0) from __main__ block, got sys.exit({exc_info.value.code!r})"
    )

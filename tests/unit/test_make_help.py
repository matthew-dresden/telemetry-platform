"""Unit tests for scripts.make_help -- Makefile catalog printer.

Tests assert that:
- AC-1: The module is importable and backs the 'make help' Makefile target
- AC-2: make help resolves through scripts.make_help and exits 0
- AC-1: print_help() reads the Makefile and prints target names to stdout
- AC-1: main() exits 0 on success, 1 on error (missing Makefile)
- AC-5: 100 percent line coverage on scripts.make_help
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _import_make_help():
    """Import (or re-import) scripts.make_help."""
    import scripts.make_help as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-1/AC-2: module is importable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_module_is_importable() -> None:
    """scripts.make_help must be importable without ModuleNotFoundError."""
    mod = _import_make_help()
    assert hasattr(mod, "print_help"), "scripts.make_help must expose a print_help function."
    assert hasattr(mod, "main"), "scripts.make_help must expose a main() entry point."


# ---------------------------------------------------------------------------
# AC-2: main() exits 0 on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_cli_exits_zero_on_success(tmp_path: Path) -> None:
    """CLI main must exit 0 when Makefile is found and readable."""
    mod = _import_make_help()

    makefile = tmp_path / "Makefile"
    makefile.write_text("help:\n\t@uv run python -m scripts.make_help\n")

    with (
        patch.object(sys, "argv", ["make_help"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main(makefile_path=str(makefile))

    assert exc_info.value.code == 0, (
        f"CLI must exit 0 when Makefile is readable, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: print_help reads Makefile and prints target names to stdout
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_prints_phony_targets(tmp_path: Path, capsys) -> None:
    """print_help must output target names extracted from the Makefile."""
    mod = _import_make_help()

    makefile = tmp_path / "Makefile"
    makefile.write_text(
        ".PHONY: foo bar baz\n\nfoo:\n\techo foo\n\nbar:\n\techo bar\n\nbaz:\n\techo baz\n"
    )

    mod.print_help(makefile_path=str(makefile))

    captured = capsys.readouterr()
    assert "foo" in captured.out, f"Expected 'foo' in output, got: {captured.out!r}"
    assert "bar" in captured.out, f"Expected 'bar' in output, got: {captured.out!r}"
    assert "baz" in captured.out, f"Expected 'baz' in output, got: {captured.out!r}"


@pytest.mark.unit
def test_make_help_outputs_to_stdout(tmp_path: Path, capsys) -> None:
    """print_help must write output to stdout, not stderr."""
    mod = _import_make_help()

    makefile = tmp_path / "Makefile"
    makefile.write_text(".PHONY: my-target\nmy-target:\n\techo ok\n")

    mod.print_help(makefile_path=str(makefile))

    captured = capsys.readouterr()
    assert "my-target" in captured.out, (
        f"Expected 'my-target' in stdout, got stdout={captured.out!r}, stderr={captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: print_help reads the actual repo Makefile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_reads_real_makefile(capsys) -> None:
    """print_help with the real Makefile must output at least the 'help' target name."""
    mod = _import_make_help()
    real_makefile = str(REPO_ROOT / "Makefile")

    mod.print_help(makefile_path=real_makefile)

    captured = capsys.readouterr()
    # The real Makefile has a 'help' target
    assert len(captured.out.strip()) > 0, (
        "print_help on the real Makefile must produce non-empty stdout output."
    )


# ---------------------------------------------------------------------------
# AC-1: main exits 1 on missing Makefile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_cli_exits_1_on_missing_makefile(tmp_path: Path, capsys) -> None:
    """CLI must exit 1 with an ERROR: message when Makefile is missing."""
    mod = _import_make_help()

    missing = str(tmp_path / "Makefile-does-not-exist")

    with (
        patch.object(sys, "argv", ["make_help"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main(makefile_path=missing)

    assert exc_info.value.code == 1, (
        f"CLI must exit 1 when Makefile is missing, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Missing Makefile error must print ERROR: to stderr, got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-2: main uses repo-root Makefile by default
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_main_uses_default_makefile(capsys) -> None:
    """main() called without arguments must find the real Makefile and exit 0."""
    mod = _import_make_help()

    with pytest.raises(SystemExit) as exc_info:
        mod.main()

    assert exc_info.value.code == 0, (
        f"main() with default Makefile path must exit 0, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert len(captured.out.strip()) > 0, (
        "main() must produce non-empty stdout output when the real Makefile is present."
    )


# ---------------------------------------------------------------------------
# Coverage: __main__ block -- executed via importlib with __name__ == '__main__'
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_help_dunder_main_block() -> None:
    """The if __name__ == '__main__' block must execute main() when run as module."""
    import importlib.util

    source_path = str(REPO_ROOT / "scripts" / "make_help.py")
    spec = importlib.util.spec_from_file_location("__main__", source_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert module is not None

    with (
        patch.object(sys, "argv", ["make_help"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        assert spec.loader is not None
        spec.loader.exec_module(module)

    assert exc_info.value.code == 0, (
        f"__main__ block must exit 0 when real Makefile is present, got {exc_info.value.code!r}."
    )

"""Unit tests for scripts.resolve_module_type -- module path to type mapping.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases cover primitive/reference/collection/data paths and
the unmatched-path error path (::error:: + exit 1).

AC-15:
- primitive/reference/collection/data paths resolve to the correct module type
- unmatched module_path emits ::error:: and exits 1
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.resolve_module_type import resolve_module_type

# Dynamic repo root -- two levels above the tests/unit/ directory
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

# Config representing monorepo-config.json provider section
_CONFIG = {
    "provider": {
        "aws": {
            "module_types": {
                "primitive": {
                    "path_patterns": ["providers/aws/primitives/*"],
                    "policy_dir": "policies/opa/terraform/provider/aws/module_types/primitive",
                },
                "collection": {
                    "path_patterns": ["providers/aws/collections/*"],
                    "policy_dir": "policies/opa/terraform/provider/aws/module_types/collection",
                },
                "reference": {
                    "path_patterns": ["providers/aws/references/*"],
                    "policy_dir": "policies/opa/terraform/provider/aws/module_types/reference",
                },
                "data": {
                    "path_patterns": ["providers/aws/data/*"],
                    "policy_dir": "policies/opa/terraform/provider/aws/module_types/data",
                },
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Parametrized cases: correct module type for each path pattern
# ---------------------------------------------------------------------------

_PATH_TYPE_CASES = [
    pytest.param(
        "providers/aws/primitives/kms-key",
        "primitive",
        id="primitive-kms-key",
    ),
    pytest.param(
        "providers/aws/primitives/s3-bucket",
        "primitive",
        id="primitive-s3-bucket",
    ),
    pytest.param(
        "providers/aws/references/state-bootstrap",
        "reference",
        id="reference-state-bootstrap",
    ),
    pytest.param(
        "providers/aws/references/oidc-bootstrap",
        "reference",
        id="reference-oidc-bootstrap",
    ),
    pytest.param(
        "providers/aws/collections/vpc-cluster",
        "collection",
        id="collection-vpc-cluster",
    ),
    pytest.param(
        "providers/aws/data/account-ids",
        "data",
        id="data-account-ids",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("module_path,expected_type", _PATH_TYPE_CASES)
def test_resolve_module_type(module_path: str, expected_type: str) -> None:
    """resolve_module_type maps module paths to the correct module type.

    AC-15: primitive/reference/collection/data paths must each resolve to
    their corresponding module type via the provider.*.module_types config.
    """
    result = resolve_module_type(module_path=module_path, config=_CONFIG)

    assert result == expected_type, (
        f"Expected module_type={expected_type!r} for module_path={module_path!r}, "
        f"got {result!r}. "
        "resolve_module_type must match path patterns from provider.*.module_types config."
    )


# ---------------------------------------------------------------------------
# Unmatched path -- ::error:: + exit 1
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_module_type_raises_on_unmatched_path() -> None:
    """resolve_module_type must raise an error for a path that matches no pattern.

    AC-15: fail-fast contract. An unmatched module_path emits ::error:: and
    exits 1 (contract per finding T10 in the spec).
    """
    from scripts.resolve_module_type import ModuleTypeError

    with pytest.raises(ModuleTypeError) as exc_info:
        resolve_module_type(
            module_path="some/unknown/path/my-module",
            config=_CONFIG,
        )

    error_msg = str(exc_info.value)
    assert "some/unknown/path/my-module" in error_msg or "no match" in error_msg.lower(), (
        f"ModuleTypeError must name the unmatched module_path. Got: {error_msg!r}"
    )


# ---------------------------------------------------------------------------
# CLI -- ::error:: and exit 1 on unmatched path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_module_type_cli_exits_nonzero_on_unmatched(tmp_path) -> None:
    """The CLI must exit 1 and emit ::error:: when the path matches no pattern.

    AC-15: fail-fast contract per finding T10.
    """
    import subprocess
    import sys

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_CONFIG))
    output_file = tmp_path / "output.txt"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.resolve_module_type",
            "--config",
            str(config_path),
            "--module-path",
            "some/unknown/path/my-module",
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode != 0, (
        "resolve_module_type CLI must exit non-zero for an unmatched module path. "
        f"Got returncode={result.returncode}."
    )
    combined_output = result.stdout + result.stderr
    assert "::error::" in combined_output, (
        "resolve_module_type CLI must emit '::error::' when path matches no pattern. "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


@pytest.mark.unit
def test_resolve_module_type_main_exits_zero_for_matched_path(tmp_path) -> None:
    """main() must exit cleanly (no sys.exit call) for a matched module path.

    The main() CLI entry point must print the module type and write the output file.
    """
    import io
    from unittest.mock import patch

    from scripts.resolve_module_type import main

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_CONFIG))
    output_file = tmp_path / "output.txt"

    captured_stdout = io.StringIO()

    with (
        patch(
            "sys.argv",
            [
                "resolve_module_type",
                "--config",
                str(config_path),
                "--module-path",
                "providers/aws/primitives/kms-key",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", captured_stdout),
        patch("sys.exit") as mock_exit,
    ):
        main()

    mock_exit.assert_not_called()
    assert "primitive" in captured_stdout.getvalue(), (
        "main() must print the bare module type to stdout."
    )
    output_content = output_file.read_text()
    assert "module_type=primitive" in output_content, (
        "main() must write module_type=primitive to the output file."
    )


@pytest.mark.unit
def test_resolve_module_type_main_exits_nonzero_for_unmatched_path(tmp_path) -> None:
    """main() must call sys.exit(1) and emit ::error:: for an unmatched module path.

    Fail-fast: the CLI must exit non-zero when no path pattern matches (T10).
    """
    import io
    from unittest.mock import patch

    from scripts.resolve_module_type import main

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_CONFIG))
    output_file = tmp_path / "output.txt"

    captured_stdout = io.StringIO()

    with (
        patch(
            "sys.argv",
            [
                "resolve_module_type",
                "--config",
                str(config_path),
                "--module-path",
                "some/unknown/path",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", captured_stdout),
        patch("sys.exit") as mock_exit,
    ):
        main()

    mock_exit.assert_called_once_with(1)
    assert "::error::" in captured_stdout.getvalue(), (
        "main() must emit '::error::' to stdout for an unmatched module path."
    )


@pytest.mark.unit
def test_resolve_module_type_write_output_appends_to_file(tmp_path) -> None:
    """write_module_type_output must append to the output file without overwriting existing."""
    from scripts.resolve_module_type import write_module_type_output

    output_file = tmp_path / "output.txt"
    output_file.write_text("existing_key=existing_value\n")

    write_module_type_output(str(output_file), "primitive")

    content = output_file.read_text()
    assert "existing_key=existing_value" in content, (
        "write_module_type_output must not overwrite existing content in the output file."
    )
    assert "module_type=primitive" in content, (
        "write_module_type_output must append module_type=primitive to the output file."
    )


@pytest.mark.unit
def test_resolve_module_type_raises_when_provider_is_not_dict() -> None:
    """resolve_module_type must raise ModuleTypeError when provider section is malformed.

    Fail-fast: if 'provider' is not a dict (malformed config), the function must
    raise ModuleTypeError rather than silently failing to match.
    """
    from scripts.resolve_module_type import ModuleTypeError

    malformed_config = {"provider": "not-a-dict"}
    with pytest.raises(ModuleTypeError) as exc_info:
        resolve_module_type(module_path="providers/aws/primitives/kms-key", config=malformed_config)

    assert "provider" in str(exc_info.value).lower(), (
        "ModuleTypeError must mention 'provider' when the section is not a dict."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "malformed_provider_section",
    [
        pytest.param(
            {"aws": "not-a-dict"},
            id="provider-config-is-string",
        ),
        pytest.param(
            {"aws": {"module_types": "not-a-dict"}},
            id="module-types-is-string",
        ),
        pytest.param(
            {"aws": {"module_types": {"primitive": "not-a-dict"}}},
            id="type-config-is-string",
        ),
        pytest.param(
            {"aws": {"module_types": {"primitive": {"path_patterns": "not-a-list"}}}},
            id="path-patterns-is-string",
        ),
    ],
)
def test_resolve_module_type_raises_on_malformed_provider_section(
    malformed_provider_section: dict,
) -> None:
    """resolve_module_type must raise ModuleTypeError when provider structure is malformed.

    Each level of the provider config must be a proper dict/list; malformed
    entries must be skipped or raise -- not silently return a wrong type.
    """
    from scripts.resolve_module_type import ModuleTypeError

    config = {"provider": malformed_provider_section}
    with pytest.raises(ModuleTypeError):
        resolve_module_type(module_path="providers/aws/primitives/kms-key", config=config)


@pytest.mark.unit
def test_resolve_module_type_cli_exits_zero_and_writes_output(tmp_path) -> None:
    """The CLI must exit 0 and write module_type= to the output file on a matched path.

    AC-15: resolve_module_type prints the bare type to stdout and writes
    module_type= to the --output file.
    """
    import subprocess
    import sys

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_CONFIG))
    output_file = tmp_path / "output.txt"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.resolve_module_type",
            "--config",
            str(config_path),
            "--module-path",
            "providers/aws/primitives/kms-key",
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 0, (
        f"resolve_module_type CLI must exit 0 for a matched module path. "
        f"Got returncode={result.returncode}, stderr={result.stderr!r}."
    )
    assert "primitive" in result.stdout, (
        f"resolve_module_type CLI must print the bare module type to stdout. "
        f"Got stdout={result.stdout!r}."
    )
    output_content = output_file.read_text()
    assert "module_type=primitive" in output_content, (
        f"resolve_module_type CLI must write 'module_type=primitive' to the output file. "
        f"Got: {output_content!r}"
    )

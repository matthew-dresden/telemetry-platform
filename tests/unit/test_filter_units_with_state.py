"""Unit tests for scripts/filter_units_with_state.py (plan-scope never-deployed filter, #244).

The filter drops change-scoped PLAN units whose remote state does NOT yet exist (never applied)
so a shared-config PR whose dependent-unit fan-out includes not-yet-deployed units keeps the
terragrunt-plan lane green -- you cannot plan what was never applied. It resolves each unit's
backend bucket+key via `terragrunt render` (zero-drift with root.hcl) and checks existence with a
single s3:HeadObject. These tests inject fake render + state-exists callables (no real terragrunt
or AWS), and cover: the split, the emitted GitHub outputs, the transparent skip log, the empty
(no-op) case, the non-S3-backend keep, and fail-fast on genuine render / state-check errors.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from scripts.filter_units_with_state import (
    SKIP_LOG_PREFIX,
    RenderError,
    StateCheckError,
    extract_backend_bucket_key,
    main,
    partition_by_state_existence,
    run,
)
from scripts.resolve_deploy_role import PathParseError

_BASE = "terragrunt/live/telemetry/us-east-1"


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _flags(*paths: str) -> str:
    parts: list[str] = []
    for p in paths:
        parts.extend(["--queue-include-dir", p])
    return " ".join(parts)


def _read_outputs(output_file: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def _rendered_s3(unit: str) -> dict[str, Any]:
    """Build a rendered-config dict for a unit with a realistic S3 backend (bucket/key)."""
    name = unit.rsplit("/", 3)[-3]  # a stable-ish token from the path
    return {
        "remote_state": {
            "backend": "s3",
            "config": {
                "bucket": f"telemetry-{name}-tfstate",
                "key": f"{unit}/terraform.tfstate",
            },
        }
    }


@pytest.fixture()
def output_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "github_output.txt"


# ---------------------------------------------------------------------------
# extract_backend_bucket_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_backend_bucket_key_s3() -> None:
    backend, bucket, key = extract_backend_bucket_key(
        _rendered_s3(f"{_BASE}/prod/000/portal/000"), "unit"
    )
    assert backend == "s3"
    assert bucket.endswith("-tfstate")
    assert key.endswith("/terraform.tfstate")


@pytest.mark.unit
def test_extract_backend_bucket_key_non_s3_returns_blank_bucket_key() -> None:
    """A local (offline) backend yields blank bucket/key so the caller keeps the unit."""
    backend, bucket, key = extract_backend_bucket_key(
        {"remote_state": {"backend": "local", "config": {"path": "terraform.tfstate"}}}, "unit"
    )
    assert backend == "local"
    assert bucket == ""
    assert key == ""


@pytest.mark.unit
def test_extract_backend_bucket_key_s3_missing_bucket_fails_fast() -> None:
    """An S3 backend with no resolved bucket/key is a genuine config error (not 'not deployed')."""
    with pytest.raises(RenderError, match="no resolved bucket/key"):
        extract_backend_bucket_key(
            {"remote_state": {"backend": "s3", "config": {"key": "k/terraform.tfstate"}}}, "unit"
        )


# ---------------------------------------------------------------------------
# partition_by_state_existence -- the split itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_keeps_deployed_skips_never_deployed() -> None:
    """A deployed unit (state exists) is kept; a never-deployed unit (no state) is skipped."""
    deployed = f"{_BASE}/prod/_singletons/shared/cloudfront-logs/000"
    never = f"{_BASE}/bootstrap/qa_role/oidc-bootstrap/000"

    def render_fn(unit: str) -> dict[str, Any]:
        return _rendered_s3(unit)

    # State exists for every bucket EXCEPT the never-deployed unit's.
    never_bucket = _rendered_s3(never)["remote_state"]["config"]["bucket"]

    def state_exists_fn(bucket: str, key: str) -> bool:
        return bucket != never_bucket

    kept, skipped = partition_by_state_existence([deployed, never], render_fn, state_exists_fn)
    assert kept == [deployed]
    assert skipped == [never]


@pytest.mark.unit
def test_partition_all_deployed_keeps_all() -> None:
    units = [f"{_BASE}/prod/000/portal/000", f"{_BASE}/prod/000/collector-ingestion/000"]
    kept, skipped = partition_by_state_existence(units, _rendered_s3, lambda _b, _k: True)
    assert kept == units
    assert skipped == []


@pytest.mark.unit
def test_partition_all_never_deployed_skips_all() -> None:
    units = [f"{_BASE}/bootstrap/qa_role/oidc-bootstrap/000"]
    kept, skipped = partition_by_state_existence(units, _rendered_s3, lambda _b, _k: False)
    assert kept == []
    assert skipped == units


@pytest.mark.unit
def test_partition_non_s3_backend_is_kept_without_state_check() -> None:
    """A non-S3 (offline/local) backend cannot be state-checked and is KEPT (never dropped)."""
    unit = f"{_BASE}/prod/000/portal/000"
    calls: list[tuple[str, str]] = []

    def render_fn(_u: str) -> dict[str, Any]:
        return {"remote_state": {"backend": "local", "config": {"path": "terraform.tfstate"}}}

    def state_exists_fn(bucket: str, key: str) -> bool:
        calls.append((bucket, key))
        return False

    kept, skipped = partition_by_state_existence([unit], render_fn, state_exists_fn)
    assert kept == [unit]
    assert skipped == []
    assert calls == []  # state check never invoked for a non-S3 backend


@pytest.mark.unit
def test_partition_propagates_render_error() -> None:
    def render_fn(_u: str) -> dict[str, Any]:
        raise RenderError("boom")

    with pytest.raises(RenderError, match="boom"):
        partition_by_state_existence(
            [f"{_BASE}/prod/000/portal/000"], render_fn, lambda _b, _k: True
        )


@pytest.mark.unit
def test_partition_propagates_state_check_error() -> None:
    def state_exists_fn(_b: str, _k: str) -> bool:
        raise StateCheckError("403 denied")

    with pytest.raises(StateCheckError, match="403 denied"):
        partition_by_state_existence(
            [f"{_BASE}/prod/000/portal/000"], _rendered_s3, state_exists_fn
        )


# ---------------------------------------------------------------------------
# run -- GitHub Actions outputs + skip log
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_writes_filtered_flags_and_has_units_true(
    output_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    deployed = f"{_BASE}/prod/000/portal/000"
    never = f"{_BASE}/bootstrap/qa_role/oidc-bootstrap/000"
    never_bucket = _rendered_s3(never)["remote_state"]["config"]["bucket"]

    run(
        include_dir_flags=_flags(deployed, never),
        output_path=str(output_file),
        render_fn=_rendered_s3,
        state_exists_fn=lambda bucket, _k: bucket != never_bucket,
    )
    outs = _read_outputs(output_file)
    assert outs["has_units"] == "true"
    assert deployed in outs["include_dir_flags"]
    assert never not in outs["include_dir_flags"]
    captured = capsys.readouterr()
    assert SKIP_LOG_PREFIX in captured.out
    assert never in captured.out


@pytest.mark.unit
def test_run_all_never_deployed_writes_has_units_false(
    output_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    never = f"{_BASE}/bootstrap/qa_role/oidc-bootstrap/000"
    run(
        include_dir_flags=_flags(never),
        output_path=str(output_file),
        render_fn=_rendered_s3,
        state_exists_fn=lambda _b, _k: False,
    )
    outs = _read_outputs(output_file)
    assert outs["has_units"] == "false"
    assert outs["include_dir_flags"] == ""


@pytest.mark.unit
def test_run_empty_flags_fails_fast(output_file: pathlib.Path) -> None:
    """An empty flags string fails fast (the workflow only invokes the filter on a non-empty
    scope; an empty scope is a real error, never a silent no-op)."""
    with pytest.raises(PathParseError):
        run(
            include_dir_flags="",
            output_path=str(output_file),
            render_fn=_rendered_s3,
            state_exists_fn=lambda _b, _k: True,
        )


# ---------------------------------------------------------------------------
# main -- exit codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_render_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, output_file: pathlib.Path
) -> None:
    """A genuine render error surfaces as a non-zero exit (fail-fast, never masked)."""

    def fake_render(_unit: str) -> dict[str, Any]:
        raise RenderError("genuine hcl error")

    monkeypatch.setattr("scripts.filter_units_with_state.render_unit_backend", fake_render)
    monkeypatch.setattr(
        "scripts.filter_units_with_state.make_s3_state_exists_fn",
        lambda _region: lambda _b, _k: True,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "filter_units_with_state",
            "--include-dir-flags",
            _flags(f"{_BASE}/prod/000/portal/000"),
            "--output",
            str(output_file),
        ],
    )
    assert main() == 1


@pytest.mark.unit
def test_main_success_exits_zero(
    monkeypatch: pytest.MonkeyPatch, output_file: pathlib.Path
) -> None:
    monkeypatch.setattr("scripts.filter_units_with_state.render_unit_backend", _rendered_s3)
    monkeypatch.setattr(
        "scripts.filter_units_with_state.make_s3_state_exists_fn",
        lambda _region: lambda _b, _k: True,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "filter_units_with_state",
            "--include-dir-flags",
            _flags(f"{_BASE}/prod/000/portal/000"),
            "--output",
            str(output_file),
        ],
    )
    assert main() == 0
    assert _read_outputs(output_file)["has_units"] == "true"


# ---------------------------------------------------------------------------
# render_unit_backend -- `terragrunt render` subprocess wrapper
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.unit
def test_render_unit_backend_parses_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful render returns the parsed JSON object from stdout."""
    import json as _json

    payload = {"remote_state": {"backend": "s3", "config": {"bucket": "b", "key": "k"}}}

    def fake_run(cmd: list[str], **_kw: Any) -> _FakeCompleted:
        assert "render" in cmd and "--format" in cmd and "json" in cmd
        return _FakeCompleted(0, stdout=_json.dumps(payload))

    monkeypatch.setattr("scripts.filter_units_with_state.subprocess.run", fake_run)
    from scripts.filter_units_with_state import render_unit_backend

    assert render_unit_backend("/some/unit") == payload


@pytest.mark.unit
def test_render_unit_backend_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero `terragrunt render` exit is a genuine config error (fail-fast, RenderError)."""

    def fake_run(_cmd: list[str], **_kw: Any) -> _FakeCompleted:
        return _FakeCompleted(1, stdout="", stderr="boom hcl error")

    monkeypatch.setattr("scripts.filter_units_with_state.subprocess.run", fake_run)
    from scripts.filter_units_with_state import render_unit_backend

    with pytest.raises(RenderError, match="boom hcl error"):
        render_unit_backend("/some/unit")


@pytest.mark.unit
def test_render_unit_backend_bad_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unparseable render stdout is a fail-fast RenderError, not a silent skip."""

    def fake_run(_cmd: list[str], **_kw: Any) -> _FakeCompleted:
        return _FakeCompleted(0, stdout="not json{")

    monkeypatch.setattr("scripts.filter_units_with_state.subprocess.run", fake_run)
    from scripts.filter_units_with_state import render_unit_backend

    with pytest.raises(RenderError, match="unparseable JSON"):
        render_unit_backend("/some/unit")


# ---------------------------------------------------------------------------
# make_s3_state_exists_fn -- HeadObject 404-vs-403 mapping (the fail-fast core)
# ---------------------------------------------------------------------------


class _FakeS3Client:
    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 (boto3 kwargs)
        self.calls.append((Bucket, Key))
        if self._error is not None:
            raise self._error
        return {"ContentLength": 123}


def _client_error(code: str, http_status: int) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": http_status}},
        "HeadObject",
    )


def _install_fake_boto3(monkeypatch: pytest.MonkeyPatch, client: _FakeS3Client) -> None:
    import boto3

    monkeypatch.setattr(boto3, "client", lambda _svc, region_name=None: client)


@pytest.mark.unit
def test_state_exists_fn_true_when_head_object_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.filter_units_with_state import make_s3_state_exists_fn

    fake = _FakeS3Client(error=None)
    _install_fake_boto3(monkeypatch, fake)
    exists = make_s3_state_exists_fn("us-east-1")
    assert exists("some-bucket", "some/key") is True
    assert fake.calls == [("some-bucket", "some/key")]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("code", "http"),
    [("404", 404), ("NoSuchKey", 404), ("NoSuchBucket", 404), ("NotFound", 404)],
)
def test_state_exists_fn_false_on_not_found(
    monkeypatch: pytest.MonkeyPatch, code: str, http: int
) -> None:
    """A definitive not-found (missing bucket/key) means the unit was never applied -> False."""
    from scripts.filter_units_with_state import make_s3_state_exists_fn

    _install_fake_boto3(monkeypatch, _FakeS3Client(error=_client_error(code, http)))
    exists = make_s3_state_exists_fn("us-east-1")
    assert exists("bucket", "key") is False


@pytest.mark.unit
def test_state_exists_fn_raises_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 AccessDenied is a genuine error, never masked as 'not deployed' (fail-fast)."""
    from scripts.filter_units_with_state import make_s3_state_exists_fn

    _install_fake_boto3(monkeypatch, _FakeS3Client(error=_client_error("AccessDenied", 403)))
    exists = make_s3_state_exists_fn("us-east-1")
    with pytest.raises(StateCheckError, match="non-not-found error"):
        exists("bucket", "key")

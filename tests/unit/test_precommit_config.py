"""Structural regression tests for .pre-commit-config.yaml.

These tests assert the structural constraints for the pre-commit hook
configuration defined in docs/release-pipeline.md, with the make-only hook policy
from docs/adr/ D29. A failure here means the hook configuration has drifted from
the canonical spec.

Assertions:
- AC-16: .pre-commit-config.yaml is a single repo: local configuration
- AC-16: every hook sets language: system
- AC-16: every hook entry is exactly make <target> with no inline shell
- AC-16: a pre-push hook runs make ci to reproduce the full CI suite locally
- AC-16: no hook entry contains a bypass affordance (--no-verify, pipe, &&, ;)
"""

import pathlib
import re

import pytest
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

_SHELL_TOKEN = re.compile(r"(\||&&|;)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Parse .pre-commit-config.yaml and return the top-level mapping.

    Raises AssertionError with an actionable message if the file is missing
    or malformed so that test collection errors are informative.
    """
    config_path = REPO_ROOT / ".pre-commit-config.yaml"
    assert config_path.exists(), (
        f".pre-commit-config.yaml not found at {config_path}; "
        "the file must be present at the repository root. "
        "Run 'make configure' to install the hook configuration."
    )
    with config_path.open() as fh:
        loaded = yaml.safe_load(fh)
    assert isinstance(loaded, dict), (
        f".pre-commit-config.yaml did not parse to a mapping. "
        f"Got type {type(loaded).__name__!r}. "
        "Ensure the file is valid YAML with a top-level mapping."
    )
    return loaded


def _get_repos(config: dict) -> list:
    """Return the repos list from a parsed pre-commit config."""
    assert "repos" in config, (
        ".pre-commit-config.yaml is missing the 'repos' key. "
        "The file must declare at least one repo stanza."
    )
    return config["repos"]


def _get_all_hooks(repos: list) -> list[dict]:
    """Return a flat list of all hook definitions across all repos."""
    hooks: list[dict] = []
    for repo in repos:
        hooks.extend(repo.get("hooks", []))
    return hooks


# ---------------------------------------------------------------------------
# Fixtures (module-scoped to avoid repeated file reads)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def precommit_config() -> dict:
    """Parse .pre-commit-config.yaml once for the whole module."""
    return _load_config()


@pytest.fixture(scope="module")
def repos(precommit_config: dict) -> list:
    """Return the repos list from .pre-commit-config.yaml."""
    return _get_repos(precommit_config)


@pytest.fixture(scope="module")
def all_hooks(repos: list) -> list[dict]:
    """Return a flat list of all hook definitions across all repos."""
    return _get_all_hooks(repos)


# ---------------------------------------------------------------------------
# Single repo: local -- AC-16 (docs/release-pipeline.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_single_repo_local(repos: list) -> None:
    """The config must contain exactly one repo stanza with repo: local.

    docs/release-pipeline.md requires a single repo: local configuration so that
    every hook is resolved against the developer's local environment and
    delegates exclusively to make targets. Multiple repo stanzas or a remote
    repo URL indicates that enforcement logic lives outside the Makefile,
    violating the make-only hook policy (docs/adr/ D29).
    """
    assert len(repos) == 1, (
        f"Expected exactly 1 repo stanza, found {len(repos)}. "
        "All hooks must be declared in a single 'repo: local' stanza "
        "(docs/release-pipeline.md, docs/adr/ D29)."
    )
    repo_url = repos[0].get("repo")
    assert repo_url == "local", (
        f"repo stanza must have repo: local, found repo: {repo_url!r}. "
        "Every hook must delegate to a make target via language: system "
        "(docs/release-pipeline.md, docs/adr/ D29)."
    )


# ---------------------------------------------------------------------------
# Every hook uses language: system -- AC-16 (docs/release-pipeline.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_hooks_use_language_system(all_hooks: list[dict]) -> None:
    """Every hook must set language: system.

    language: system means the hook runs the entry command directly in the
    developer's shell PATH, where the make command is always available.
    Using any other language (e.g. python, node, script) would cause
    pre-commit to create its own managed environment, which could diverge
    from the make-driven toolchain (docs/release-pipeline.md, docs/adr/ D29).
    """
    assert all_hooks, (
        "No hooks found in .pre-commit-config.yaml. At least one hook must be declared."
    )
    violations: list[str] = []
    for hook in all_hooks:
        hook_id = hook.get("id", "<unnamed>")
        language = hook.get("language")
        if language != "system":
            violations.append(f"  hook {hook_id!r} has language: {language!r}, expected 'system'")
    assert not violations, (
        f"Found {len(violations)} hook(s) not using language: system "
        f"(violates docs/release-pipeline.md, docs/adr/ D29):\n"
        + "\n".join(violations)
        + "\nAll hooks must use language: system so the entry runs directly in the shell."
    )


# ---------------------------------------------------------------------------
# Every entry is exactly make <target> -- AC-16 (docs/adr/ D29)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_entries_are_bare_make_targets(all_hooks: list[dict]) -> None:
    """Every hook entry must be exactly 'make <target>' with no inline shell.

    The make-only hook policy (docs/adr/ D29) requires that no enforcement logic
    lives in the hook configuration. The entry must be a bare 'make <target>'
    so that all logic evolves in exactly one place -- the Makefile. Entries
    with inline shell (pipes, &&, ;) or that are not make commands are
    rejected because they bifurcate the task interface.
    """
    assert all_hooks, (
        "No hooks found in .pre-commit-config.yaml. At least one hook must be declared."
    )
    violations: list[str] = []
    for hook in all_hooks:
        entry = hook.get("entry", "")
        hook_id = hook.get("id", "<unnamed>")
        if not entry.startswith("make "):
            violations.append(f"  hook {hook_id!r}: entry {entry!r} does not start with 'make '")
        else:
            shell_match = _SHELL_TOKEN.search(entry)
            if shell_match:
                violations.append(
                    f"  hook {hook_id!r}: entry {entry!r} contains shell token "
                    f"{shell_match.group()!r} at position {shell_match.start()}"
                )
    assert not violations, (
        f"Found {len(violations)} hook(s) with non-make or inline-shell entries "
        f"(violates docs/adr/ D29):\n"
        + "\n".join(violations)
        + "\nAll entries must be bare 'make <target>' with no shell tokens."
    )


# ---------------------------------------------------------------------------
# No bypass affordances in any entry -- AC-16 (docs/adr/ D29)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_bypass_affordance_in_entries(all_hooks: list[dict]) -> None:
    """No hook entry may contain a --no-verify-style bypass affordance.

    A hook that can be bypassed negates the enforcement guarantee. The entry
    must never reference --no-verify or any equivalent bypass flag. Hooks
    are never to be skipped (docs/adr/ D29, docs/release-pipeline.md).
    """
    violations: list[str] = []
    for hook in all_hooks:
        hook_id = hook.get("id", "<unnamed>")
        entry = hook.get("entry", "")
        if "--no-verify" in entry:
            violations.append(f"  hook {hook_id!r}: entry {entry!r} contains '--no-verify' bypass")
    assert not violations, (
        f"Found {len(violations)} hook(s) with bypass affordances (violates docs/adr/ D29):\n"
        + "\n".join(violations)
        + "\nNo hook may provide a bypass mechanism. Remove '--no-verify' from entries."
    )


# ---------------------------------------------------------------------------
# pre-push hook runs make ci -- AC-16 (docs/adr/ D29)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prepush_hook_runs_make_ci(all_hooks: list[dict]) -> None:
    """A hook registered for the pre-push stage must run 'make ci'.

    docs/adr/ D29 requires that the pre-push stage reproduces the full CI suite
    locally so that no code leaves the developer's machine without passing
    the complete gate set. The entry for the pre-push hook must be exactly
    'make ci' -- any other target would fail to reproduce CI.
    """
    prepush_hooks = [h for h in all_hooks if "pre-push" in h.get("stages", [])]
    assert prepush_hooks, (
        "No hook is registered for the 'pre-push' stage. "
        "A pre-push hook running 'make ci' is required to reproduce the full "
        "CI suite locally (docs/adr/ D29, docs/release-pipeline.md). "
        "Add a hook with stages: [pre-push] and entry: make ci."
    )
    ci_hooks = [h for h in prepush_hooks if h.get("entry") == "make ci"]
    assert ci_hooks, (
        f"Found {len(prepush_hooks)} pre-push hook(s) but none has entry 'make ci'. "
        f"Pre-push hook entries: {[h.get('entry') for h in prepush_hooks]!r}. "
        "The pre-push hook must invoke exactly 'make ci' to reproduce CI locally "
        "(docs/adr/ D29)."
    )


# ---------------------------------------------------------------------------
# At least one pre-commit stage hook for fast per-scope gates -- AC-16
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_precommit_hooks_present(all_hooks: list[dict]) -> None:
    """At least one hook must be registered for the pre-commit stage.

    docs/release-pipeline.md requires fast per-scope gates on the pre-commit stage
    to give developers quick feedback. Without any pre-commit hooks, the only
    local enforcement is the pre-push gate, which is too slow for the
    developer feedback loop.
    """
    precommit_hooks = [h for h in all_hooks if "pre-commit" in h.get("stages", [])]
    assert precommit_hooks, (
        "No hooks are registered for the 'pre-commit' stage. "
        "Fast per-scope gates on the pre-commit stage are required for quick "
        "developer feedback (docs/release-pipeline.md). "
        "Add at least one hook with stages: [pre-commit]."
    )


# ---------------------------------------------------------------------------
# Parametrized per-hook tests using actual hook ids -- AC-16
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Dynamically parametrize tests that declare a hook_entry or hook_spec fixture.

    This avoids hardcoded range() + pytest.skip patterns. The parametrize
    ids match the hook ids from the config, making failures easy to trace.
    """
    if "hook_spec" in metafunc.fixturenames:
        config = _load_config()
        repos = _get_repos(config)
        hooks = _get_all_hooks(repos)
        ids = [h.get("id", f"hook_{i}") for i, h in enumerate(hooks)]
        metafunc.parametrize("hook_spec", hooks, ids=ids)


@pytest.mark.unit
def test_each_hook_language_system(hook_spec: dict) -> None:
    """Each hook, addressed by id, must have language: system.

    This per-hook parametrized test complements test_all_hooks_use_language_system
    by surfacing the failing hook id directly in the test output, making it
    easier to identify the root cause when the hook set grows.
    """
    hook_id = hook_spec.get("id", "<unnamed>")
    language = hook_spec.get("language")
    assert language == "system", (
        f"Hook {hook_id!r} has language: {language!r}, expected 'system'. "
        "All hooks must use language: system (docs/release-pipeline.md, docs/adr/ D29)."
    )


@pytest.mark.unit
def test_each_hook_entry_is_make_target(hook_spec: dict) -> None:
    """Each hook entry must start with 'make ' and contain no shell tokens.

    Per-hook parametrized test that surfaces the exact failing hook id and
    entry value in the test name, complementing test_all_entries_are_bare_make_targets.
    """
    hook_id = hook_spec.get("id", "<unnamed>")
    entry = hook_spec.get("entry", "")
    assert entry.startswith("make "), (
        f"Hook {hook_id!r} entry {entry!r} does not start with 'make '. "
        "Every hook entry must be exactly 'make <target>' (docs/adr/ D29)."
    )
    shell_match = _SHELL_TOKEN.search(entry)
    assert not shell_match, (
        f"Hook {hook_id!r} entry {entry!r} contains shell token "
        f"{shell_match.group()!r}. "
        "No inline shell is permitted in hook entries (docs/adr/ D29)."
    )


@pytest.mark.unit
def test_each_hook_no_bypass(hook_spec: dict) -> None:
    """Each hook entry must not contain --no-verify or bypass affordances.

    Per-hook parametrized test surfacing exact hook id and entry in failures.
    """
    hook_id = hook_spec.get("id", "<unnamed>")
    entry = hook_spec.get("entry", "")
    assert "--no-verify" not in entry, (
        f"Hook {hook_id!r} entry {entry!r} contains '--no-verify' bypass affordance. "
        "No hook may provide a bypass mechanism (docs/adr/ D29)."
    )

"""Static workflow contract tests for CI hardening (E10-F2-S4-T1).

Asserts the structural constraints for:
  - AC-1: the tf-test job in validations.yml ends with an if: always() step running
    make terratest-sweep SWEEP_MODE=delete under the same QA OIDC credentials
  - AC-1: terratest-sweep.yml exists with schedule + workflow_dispatch triggers,
    QA OIDC role assumption, delete-then-check sweep steps, QA-only scope
    (no prod/root; no destroy step)
  - AC-1: terragrunt-apply.yml has a second configure-aws-credentials step with
    role-chaining: true to the root dns-writer role for root units, and NO destroy step
  - AC-2: all actions in the new/edited workflows are SHA-pinned (40-char hex SHA
    with trailing version comment)
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

VALIDATIONS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validations.yml"
SWEEP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terratest-sweep.yml"
APPLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terragrunt-apply.yml"

# SHA-40 pattern (all third-party uses: must match this)
_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")

# Root dns-writer role ARN (D-11)
# The dns-writer ARN is resolved from config (env_accounts.json + accounts.json) by the
# resolve-role step and consumed via its output -- never hardcoded as a 12-digit account ARN.
DNS_WRITER_ARN_OUTPUT = "steps.resolve-role.outputs.dns_writer_arn"
NEEDS_DNS_WRITER_OUTPUT = "steps.resolve-role.outputs.needs_dns_writer"
_HARDCODED_DNS_WRITER_ARN = "arn:aws:iam::444444444444:role/telemetry-platform-dns-writer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_yaml(path: pathlib.Path) -> dict:
    """Load and return a workflow YAML file, asserting it exists first."""
    assert path.exists(), (
        f"Workflow file not found: {path}. "
        "This file must exist per the Changes Manifest (E10-F2-S4-T1)."
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _check_sha_pinned(path: pathlib.Path, text: str) -> None:
    """Assert every third-party uses: in text is SHA-pinned with a version comment.

    First-party ./actions are exempt.
    """
    uses_re = re.compile(r"^\s*uses:\s+(.+)$", re.MULTILINE)
    for match in uses_re.finditer(text):
        ref = match.group(1).split("#")[0].strip()
        if ref.startswith("./"):
            continue
        line_num = text[: match.start()].count("\n") + 1
        assert "@" in ref, (
            f"{path.name} line {line_num}: uses: '{ref}' has no '@' separator -- "
            "all third-party actions must be SHA-pinned (AC-2, E10-F2-S4-T1)."
        )
        _action, sha_part = ref.rsplit("@", 1)
        assert _SHA40.match(sha_part), (
            f"{path.name} line {line_num}: uses: '{ref}' is not pinned to a 40-char SHA "
            f"(got '{sha_part}') -- pin to a full commit SHA (AC-2, E10-F2-S4-T1)."
        )
        line_text = text.splitlines()[line_num - 1]
        assert "#" in line_text, (
            f"{path.name} line {line_num}: SHA-pinned action '{ref}' is missing a trailing "
            "version comment (e.g. '# v4.4.0') (AC-2, E10-F2-S4-T1)."
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def validations_text() -> str:
    """Read validations.yml."""
    assert VALIDATIONS_WORKFLOW.exists(), f"validations.yml not found at {VALIDATIONS_WORKFLOW}."
    return VALIDATIONS_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def validations_yaml(validations_text: str) -> dict:
    """Parse validations.yml as YAML."""
    return yaml.safe_load(validations_text)


@pytest.fixture(scope="module")
def sweep_text() -> str:
    """Read terratest-sweep.yml. Fails if the file does not exist."""
    assert SWEEP_WORKFLOW.exists(), (
        f"terratest-sweep.yml not found at {SWEEP_WORKFLOW}. "
        "This file must be added by E10-F2-S4-T1 (AC-1)."
    )
    return SWEEP_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sweep_yaml(sweep_text: str) -> dict:
    """Parse terratest-sweep.yml as YAML."""
    return yaml.safe_load(sweep_text)


@pytest.fixture(scope="module")
def apply_text() -> str:
    """Read terragrunt-apply.yml."""
    assert APPLY_WORKFLOW.exists(), f"terragrunt-apply.yml not found at {APPLY_WORKFLOW}."
    return APPLY_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def apply_yaml(apply_text: str) -> dict:
    """Parse terragrunt-apply.yml as YAML."""
    return yaml.safe_load(apply_text)


# ===========================================================================
# AC-1a: validations.yml -- tf-test job always() sweep step
# ===========================================================================


@pytest.mark.unit
def test_validations_tf_test_job_has_always_sweep_step(validations_yaml: dict) -> None:
    """The tf-test job must end with an if: always() step running
    make terratest-sweep SWEEP_MODE=delete (AC-1, spec 4.9)."""
    jobs = validations_yaml.get("jobs", {})
    assert "tf-test" in jobs, (
        "validations.yml must have a 'tf-test' job (spec section 1.6, E10-F2-S4-T1)."
    )
    tf_test_steps = jobs["tf-test"].get("steps", [])
    assert tf_test_steps, "tf-test job must have at least one step."
    last_step = tf_test_steps[-1]
    step_if = last_step.get("if", "")
    assert "always()" in str(step_if), (
        f"The last step of tf-test must have 'if: always()' (AC-1). Got if: '{step_if}'."
    )


@pytest.mark.unit
def test_validations_tf_test_sweep_step_runs_delete_mode(validations_yaml: dict) -> None:
    """The always() sweep step in tf-test must run make terratest-sweep SWEEP_MODE=delete
    (AC-1, spec 4.9)."""
    jobs = validations_yaml.get("jobs", {})
    tf_test_steps = jobs.get("tf-test", {}).get("steps", [])
    always_steps = [s for s in tf_test_steps if "always()" in str(s.get("if", ""))]
    assert always_steps, (
        "tf-test job must have at least one step with if: always() (AC-1, spec 4.9)."
    )
    sweep_step_text = str(always_steps[-1].get("run", ""))
    assert "make terratest-sweep" in sweep_step_text, (
        f"The always() step must run 'make terratest-sweep'. Got: '{sweep_step_text}'."
    )
    assert "SWEEP_MODE=delete" in sweep_step_text, (
        f"The always() sweep step must set SWEEP_MODE=delete. Got: '{sweep_step_text}'."
    )


@pytest.mark.unit
def test_validations_sweep_step_has_shell_bash(validations_yaml: dict) -> None:
    """The always() sweep step must declare shell: bash (CLAUDE.md GitHub Actions standard)."""
    jobs = validations_yaml.get("jobs", {})
    tf_test_steps = jobs.get("tf-test", {}).get("steps", [])
    always_steps = [s for s in tf_test_steps if "always()" in str(s.get("if", ""))]
    assert always_steps, "tf-test must have an if: always() step."
    last = always_steps[-1]
    assert last.get("shell") == "bash", (
        f"The always() sweep step must declare 'shell: bash'. Got: '{last.get('shell')}'."
    )


# ===========================================================================
# AC-1b: terratest-sweep.yml existence, triggers, QA-only scope
# ===========================================================================


@pytest.mark.unit
def test_sweep_workflow_exists() -> None:
    """terratest-sweep.yml must exist (AC-1, E10-F2-S4-T1)."""
    assert SWEEP_WORKFLOW.exists(), (
        f"terratest-sweep.yml not found at {SWEEP_WORKFLOW}. "
        "Add the file per the Changes Manifest (E10-F2-S4-T1)."
    )


@pytest.mark.unit
def test_sweep_workflow_has_schedule_trigger(sweep_yaml: dict) -> None:
    """terratest-sweep.yml must trigger on a daily schedule (AC-1, spec 4.9)."""
    on_triggers = sweep_yaml.get(True) or sweep_yaml.get("on") or {}
    assert "schedule" in on_triggers, (
        "terratest-sweep.yml must declare a 'schedule' trigger for daily runs (AC-1, spec 4.9)."
    )
    schedule_entries = on_triggers["schedule"]
    assert schedule_entries, "schedule trigger must have at least one cron entry."
    cron_val = schedule_entries[0].get("cron", "")
    assert cron_val, (
        "terratest-sweep.yml schedule trigger must specify a cron expression (AC-1, spec 4.9)."
    )


@pytest.mark.unit
def test_sweep_workflow_has_workflow_dispatch_trigger(sweep_yaml: dict) -> None:
    """terratest-sweep.yml must also trigger on workflow_dispatch (AC-1, spec 4.9)."""
    on_triggers = sweep_yaml.get(True) or sweep_yaml.get("on") or {}
    assert "workflow_dispatch" in on_triggers, (
        "terratest-sweep.yml must declare a 'workflow_dispatch' trigger (AC-1, spec 4.9)."
    )


@pytest.mark.unit
def test_sweep_workflow_assumes_qa_oidc_role(sweep_text: str) -> None:
    """terratest-sweep.yml must assume the QA OIDC role (AWS_QA_TERRATEST_ROLE_ARN)
    (AC-1, spec 4.9)."""
    assert "AWS_QA_TERRATEST_ROLE_ARN" in sweep_text, (
        "terratest-sweep.yml must reference vars.AWS_QA_TERRATEST_ROLE_ARN for QA OIDC "
        "role assumption (AC-1, spec 4.9). Sandbox sweeps locally; prod/root never swept."
    )


@pytest.mark.unit
def test_sweep_workflow_has_delete_step(sweep_text: str) -> None:
    """terratest-sweep.yml must have a step running make terratest-sweep SWEEP_MODE=delete
    (AC-1, spec 4.9)."""
    assert "make terratest-sweep" in sweep_text and "SWEEP_MODE=delete" in sweep_text, (
        "terratest-sweep.yml must contain a step running "
        "'make terratest-sweep SWEEP_MODE=delete' (AC-1, spec 4.9)."
    )


@pytest.mark.unit
def test_sweep_workflow_has_check_step_after_delete(sweep_text: str) -> None:
    """terratest-sweep.yml must run SWEEP_MODE=check after SWEEP_MODE=delete (AC-1, spec 4.9).
    The check step goes red on any residue."""
    delete_pos = sweep_text.find("SWEEP_MODE=delete")
    check_pos = sweep_text.find("SWEEP_MODE=check")
    assert delete_pos >= 0, "terratest-sweep.yml must contain SWEEP_MODE=delete step (AC-1)."
    assert check_pos >= 0, (
        "terratest-sweep.yml must contain SWEEP_MODE=check step (AC-1, spec 4.9). "
        "The check step goes red on residue."
    )
    assert delete_pos < check_pos, (
        "SWEEP_MODE=delete must appear before SWEEP_MODE=check in terratest-sweep.yml (AC-1)."
    )


@pytest.mark.unit
def test_sweep_workflow_has_no_destroy_step(sweep_text: str) -> None:
    """terratest-sweep.yml must not contain any Terragrunt destroy step (D-14 posture).
    Prod/root are never swept."""
    assert "tg-destroy" not in sweep_text and "terragrunt destroy" not in sweep_text, (
        "terratest-sweep.yml must not contain any Terragrunt destroy invocation (D-14). "
        "Prod and root accounts are never swept."
    )


@pytest.mark.unit
def test_sweep_workflow_uses_oidc_permissions(sweep_yaml: dict) -> None:
    """terratest-sweep.yml must grant id-token: write for OIDC authentication (AC-1)."""
    top_perms = sweep_yaml.get("permissions", {})
    jobs = sweep_yaml.get("jobs", {})
    has_permission = top_perms.get("id-token") == "write"
    if not has_permission:
        for _job_name, job_def in jobs.items():
            if isinstance(job_def, dict):
                job_perms = job_def.get("permissions", {}) or {}
                if job_perms.get("id-token") == "write":
                    has_permission = True
                    break
    assert has_permission, (
        "terratest-sweep.yml must grant 'id-token: write' for OIDC authentication (AC-1)."
    )


@pytest.mark.unit
def test_sweep_workflow_all_run_steps_have_shell_bash(sweep_yaml: dict) -> None:
    """Every run: step in terratest-sweep.yml must declare shell: bash
    (CLAUDE.md GitHub Actions standard)."""
    jobs = sweep_yaml.get("jobs", {})
    violations = []
    for job_name, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        for i, step in enumerate(job_def.get("steps", [])):
            if "run" in step and step.get("shell") != "bash":
                violations.append(f"job '{job_name}' step {i + 1} (name={step.get('name', '?')})")
    assert not violations, (
        f"The following run: steps in terratest-sweep.yml are missing 'shell: bash': "
        f"{violations} (CLAUDE.md GitHub Actions standard)."
    )


# ===========================================================================
# AC-1c: terragrunt-apply.yml -- per-account partition, root role-chaining, no destroy
# ===========================================================================


@pytest.mark.unit
def test_apply_workflow_uses_single_ambient_role_no_clobbering_chain(apply_text: str) -> None:
    """terragrunt-apply.yml must assume EXACTLY ONE ambient role (the primary account's apply
    role) and must NOT add a second configure-aws-credentials role-chaining step.

    Multi-account CI WALL 2 (supersedes the old D-11 workflow-level dns-writer chain): the old
    second configure-aws-credentials step (role-chaining: true to dns-writer) left dns-writer as
    the AMBIENT identity, so the prod state preflight + apply ran as dns-writer and could not
    DescribeKey the prod state CMK. Cross-account routing is now done per-unit inside root.hcl
    (provider+backend assume_role to the unit-account deploy role) under TG_CI_PRIMARY_ACCOUNT_ID,
    so the runner holds a single, never-clobbered ambient role for the whole run.
    """
    assert "role-chaining: true" not in apply_text, (
        "terragrunt-apply.yml must NOT add a workflow-level 'role-chaining: true' step -- it "
        "clobbered the primary apply role as ambient creds (WALL 2 root cause). Cross-account "
        "routing is per-unit in root.hcl (provider/backend assume_role) under "
        "TG_CI_PRIMARY_ACCOUNT_ID."
    )
    # Exactly one configure-aws-credentials step (the primary apply role).
    assert apply_text.count("aws-actions/configure-aws-credentials") == 1, (
        "terragrunt-apply.yml must configure AWS credentials exactly once (the primary account "
        "apply role); a second clobbering step is the WALL 2 root cause."
    )


@pytest.mark.unit
def test_apply_workflow_exports_primary_account_for_cross_account_routing(apply_text: str) -> None:
    """terragrunt-apply.yml must export TG_CI_PRIMARY_ACCOUNT_ID from the resolved target account
    so root.hcl can generate per-unit assume_role for cross-account (dns-owner) units (WALL 2)."""
    assert "TG_CI_PRIMARY_ACCOUNT_ID" in apply_text, (
        "terragrunt-apply.yml must export 'TG_CI_PRIMARY_ACCOUNT_ID' so root.hcl routes "
        "cross-account units' provider/backend to the unit-account deploy role (WALL 2)."
    )
    assert "steps.resolve-role.outputs.target_account_id" in apply_text, (
        "terragrunt-apply.yml must source TG_CI_PRIMARY_ACCOUNT_ID from "
        "'steps.resolve-role.outputs.target_account_id' (the config-resolved primary account, "
        "account abstracted out per D2)."
    )
    assert _HARDCODED_DNS_WRITER_ARN not in apply_text, (
        "terragrunt-apply.yml must NOT hardcode the 12-digit dns-writer ARN "
        f"'{_HARDCODED_DNS_WRITER_ARN}'; cross-account routing is resolved from config (D2)."
    )


@pytest.mark.unit
def test_apply_workflow_has_no_destroy_step(apply_text: str) -> None:
    """terragrunt-apply.yml must not contain any destroy step (AC-1, spec D-14)."""
    assert "tg-destroy" not in apply_text, (
        "terragrunt-apply.yml must NOT contain a 'tg-destroy' step -- "
        "prod/root are never destroyed by CI (AC-1, spec D-14, E10-F2-S4-T1)."
    )
    assert "terragrunt destroy" not in apply_text, (
        "terragrunt-apply.yml must NOT invoke 'terragrunt destroy' -- "
        "prod/root are never destroyed by CI (AC-1, spec D-14)."
    )


@pytest.mark.unit
def test_apply_workflow_role_chaining_step_has_shell_bash_or_uses(apply_yaml: dict) -> None:
    """Every run: step in terragrunt-apply.yml must declare shell: bash (CLAUDE.md)."""
    jobs = apply_yaml.get("jobs", {})
    violations = []
    for job_name, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        for i, step in enumerate(job_def.get("steps", [])):
            if "run" in step and step.get("shell") != "bash":
                violations.append(f"job '{job_name}' step {i + 1} (name={step.get('name', '?')})")
    assert not violations, (
        f"The following run: steps in terragrunt-apply.yml are missing 'shell: bash': "
        f"{violations} (CLAUDE.md GitHub Actions standard)."
    )


@pytest.mark.unit
def test_apply_workflow_primary_account_exported_before_preflight(apply_text: str) -> None:
    """TG_CI_PRIMARY_ACCOUNT_ID must be exported AFTER credential configuration and BEFORE the
    state preflight + apply, so root.hcl sees it for every cross-account unit (WALL 2)."""
    creds_pos = apply_text.find("aws-actions/configure-aws-credentials")
    export_pos = apply_text.find("TG_CI_PRIMARY_ACCOUNT_ID")
    preflight_pos = apply_text.find("make tf-state-preflight")
    apply_pos = apply_text.find("make tg-apply")
    assert creds_pos >= 0 and export_pos >= 0 and preflight_pos >= 0 and apply_pos >= 0, (
        "terragrunt-apply.yml must contain configure-aws-credentials, TG_CI_PRIMARY_ACCOUNT_ID "
        "export, tf-state-preflight, and tg-apply (WALL 2)."
    )
    assert creds_pos < export_pos < preflight_pos < apply_pos, (
        "terragrunt-apply.yml ordering must be: configure-aws-credentials -> export "
        "TG_CI_PRIMARY_ACCOUNT_ID -> tf-state-preflight -> tg-apply (WALL 2)."
    )


# ===========================================================================
# module-validate: static tf-plan dropped (terratest covers plan+apply+idempotency+destroy)
# ===========================================================================


# The static tf-plan step (and the configure-aws-credentials step that existed
# only to give it qa credentials) was removed from the module-validate job. A
# static plan cannot safely obtain qa credentials on un-gated PRs, and the gated
# tf-test job already covers a real plan -> apply -> idempotency -> destroy cycle.
# These tests assert both steps are ABSENT from module-validate, while the
# credential step stays in the tf-test job.
_CONFIGURE_AWS_ACTION = "aws-actions/configure-aws-credentials"


def _module_validate_steps(validations_yaml: dict) -> list:
    """Return the module-validate job's step list, asserting the job exists."""
    jobs = validations_yaml.get("jobs", {})
    assert "module-validate" in jobs, "validations.yml must have a 'module-validate' job."
    steps = jobs["module-validate"].get("steps", [])
    assert steps, "module-validate job must have at least one step."
    return steps


def _step_index(steps: list, predicate) -> int:
    """Return the index of the first step matching predicate, or -1 if none."""
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    return -1


@pytest.mark.unit
def test_module_validate_has_no_configure_aws_credentials_step(validations_yaml: dict) -> None:
    """module-validate must NOT include a configure-aws-credentials step.

    The creds step existed only to feed the (now removed) static tf-plan; an
    un-gated PR cannot safely assume the qa role, so it was dropped. Credentials
    live only in the gated tf-test job.
    """
    steps = _module_validate_steps(validations_yaml)
    creds_idx = _step_index(steps, lambda s: _CONFIGURE_AWS_ACTION in str(s.get("uses", "")))
    assert creds_idx < 0, (
        "module-validate must NOT include an 'aws-actions/configure-aws-credentials' step; "
        "static tf-plan was dropped and credentials belong only to the gated tf-test job."
    )


@pytest.mark.unit
def test_module_validate_does_not_run_tf_plan(validations_yaml: dict) -> None:
    """tf-plan must NOT be a module-validate step (terratest covers plan+apply+idempotency)."""
    steps = _module_validate_steps(validations_yaml)
    tf_plan_idx = _step_index(steps, lambda s: "make tf-plan" in str(s.get("run", "")))
    assert tf_plan_idx < 0, (
        "module-validate must NOT run 'make tf-plan'; the static plan was dropped because "
        "the gated tf-test job covers a real plan -> apply -> idempotency -> destroy cycle."
    )


@pytest.mark.unit
def test_tf_test_still_has_configure_aws_credentials_step(validations_yaml: dict) -> None:
    """The tf-test job keeps its configure-aws-credentials step (qa OIDC for terratest)."""
    jobs = validations_yaml.get("jobs", {})
    assert "tf-test" in jobs, "validations.yml must have a 'tf-test' job."
    tt_steps = jobs["tf-test"].get("steps", [])
    creds_idx = _step_index(tt_steps, lambda s: _CONFIGURE_AWS_ACTION in str(s.get("uses", "")))
    assert creds_idx >= 0, (
        "tf-test must keep its 'aws-actions/configure-aws-credentials' step so terratest can "
        "assume the qa OIDC role."
    )


@pytest.mark.unit
def test_module_validate_run_steps_have_shell_bash(validations_yaml: dict) -> None:
    """Every run: step in module-validate must declare shell: bash (CLAUDE.md standard)."""
    steps = _module_validate_steps(validations_yaml)
    violations = [
        f"step {i + 1} (name={s.get('name', '?')})"
        for i, s in enumerate(steps)
        if "run" in s and s.get("shell") != "bash"
    ]
    assert not violations, (
        f"module-validate run: steps missing 'shell: bash': {violations} (CLAUDE.md standard)."
    )


# ===========================================================================
# AC-2: SHA-pin checks for all edited/added workflow files
# ===========================================================================


@pytest.mark.unit
def test_validations_workflow_actions_are_sha_pinned(validations_text: str) -> None:
    """All third-party uses: in validations.yml must be SHA-pinned with version comment
    (AC-2, spec AC #20)."""
    _check_sha_pinned(VALIDATIONS_WORKFLOW, validations_text)


@pytest.mark.unit
def test_sweep_workflow_actions_are_sha_pinned(sweep_text: str) -> None:
    """All third-party uses: in terratest-sweep.yml must be SHA-pinned with version comment
    (AC-2, spec AC #20)."""
    _check_sha_pinned(SWEEP_WORKFLOW, sweep_text)


@pytest.mark.unit
def test_apply_workflow_actions_are_sha_pinned(apply_text: str) -> None:
    """All third-party uses: in terragrunt-apply.yml must be SHA-pinned with version comment
    (AC-2, spec AC #20)."""
    _check_sha_pinned(APPLY_WORKFLOW, apply_text)

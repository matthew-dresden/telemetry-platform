# Repo-root Makefile -- the single task interface for all dev, admin, CI, and CD work.
# Every target wraps `uv run` or `uv run python -m scripts.<x>` (ledger D11).
# No recipe may contain an inline shell pipeline that performs logic.
# Coverage thresholds are read from monorepo-config.json at invocation time (ledger D22).

# ---------------------------------------------------------------------------
# Coverage threshold variables -- read from monorepo-config.json (ledger D22)
# ---------------------------------------------------------------------------
PY_COVERAGE_MIN  := $(shell uv run python -c "import json,sys; d=json.load(open('monorepo-config.json')); sys.stdout.write(str(d['coverage_thresholds']['python_cov_min']))")
GO_COVERAGE_MIN  := $(shell uv run python -c "import json,sys; d=json.load(open('monorepo-config.json')); sys.stdout.write(str(d['coverage_thresholds']['go_cov_min']))")
REGO_COVERAGE_MIN := $(shell uv run python -c "import json,sys; d=json.load(open('monorepo-config.json')); sys.stdout.write(str(d['coverage_thresholds']['rego_cov_min']))")

.PHONY: \
	ci validate configure \
	tools-ensure py-sync hooks-install help \
	py-format py-format-check py-lint py-typecheck py-security py-test python-quality \
	go-format go-lint go-vuln go-unit-test-coverage go-unit-test-coverage-json \
	rego-format rego-lint rego-unit-test-coverage rego-unit-test-coverage-json \
	tf-format tf-format-check tf-lint tf-security tf-docs-check module-validate \
	tf-plan tf-test tf-validate \
	tg-format-check tg-validate tg-security tg-plan tg-apply tg-destroy \
	tg-detect-units tg-detect-apply-units tg-filter-deployable-units tg-filter-deployed-units tg-resolve-deploy-role tg-partition-units tg-select-partition-row tg-output tg-regression \
	tf-state-preflight tf-validate-dependency-paths tf-guard-pinned-sources tf-guard-const-sources tf-guard-version-floor \
	tg-bucket-name-unique tg-no-hardcoded-identity tg-copyability-test \
	terratest-tags-check terratest-coverage-check \
	yaml-format-check yaml-lint actionlint actions-sha-pin-check \
	md-lint \
	scope-detect check-required-var-wiring resolve-module-type validate-pr-title calculate-version \
	check-version-immutability simulate-merge check-staleness update-version \
	generate-changelog lock-branch safety-net-unlock check-release-commit \
	check-scope-override git-fetch git-reset-hard git-identity publish-release \
	required-checks-aggregate detect-module-changes \
	terratest-sweep terratest-sweep-module \
	git-history-check \
	live-verify \
	bootstrap-oidc-provider \
	e2e-loadgen e2e-verify e2e-all e2e-queryability \
	perf-loadgen perf-report perf-merge-results perf-waf-allowlist-apply perf-waf-allowlist-remove

# ---------------------------------------------------------------------------
# Top-level orchestrators (ledger D29)
# ---------------------------------------------------------------------------

# THE single full-suite target -- identical to what CI runs (ledger D29).
# Includes all quality gates across all scopes. The pre-push hook runs this.
ci: python-quality \
	yaml-format-check yaml-lint actionlint actions-sha-pin-check \
	md-lint \
	rego-format rego-lint rego-unit-test-coverage \
	go-format go-lint go-vuln go-unit-test-coverage \
	tg-no-hardcoded-identity tg-copyability-test \
	tf-guard-pinned-sources tf-guard-const-sources tf-guard-version-floor \
	terratest-tags-check \
	terratest-coverage-check

# Alias of ci (ledger D29 -- retained name, identical gate sequence).
validate: ci

# One-time developer bootstrap: provision tools + sync Python env + install hooks.
configure: tools-ensure py-sync hooks-install

# ---------------------------------------------------------------------------
# Setup / provisioning targets
# ---------------------------------------------------------------------------

# Verify/provision the pinned toolchain by downloading official pinned release binaries.
# Reads versions from .tool-versions. No-op when versions already match (ledger D28).
tools-ensure:
	uv run python -m scripts.ensure_tools

# Materialize the Python environment from uv.lock.
py-sync:
	uv sync --frozen

# Install the make-only git hooks (pre-commit + pre-push) (ledger D29).
hooks-install:
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

# List all available targets.
help:
	@uv run python -m scripts.make_help

# ---------------------------------------------------------------------------
# Python quality gates
# ---------------------------------------------------------------------------

py-format:
	uv run ruff format scripts tests

py-format-check:
	uv run ruff format --check scripts tests

py-lint:
	uv run ruff check scripts tests

py-typecheck:
	uv run mypy scripts

# Scoped bandit baseline -- two-pass approach (AC-BANDIT-SCOPE-001, AC-BANDIT-SCOPE-004):
#
# Pass 1 (scripts/ensure_tools.py with baseline):
#   scripts/ensure_tools.py legitimately uses subprocess.run with list-based commands
#   (no shell=True, no user-controlled input) to probe tool versions, download official
#   pinned release binaries via curl (resolved by absolute path, --proto =https), and
#   run install steps. Bandit fires these LOW false positives:
#     B404 (import subprocess) -- one site
#     B603 (subprocess call without shell=True check) -- two sites (version probe + curl)
#   .bandit-baseline.json records exactly these B404/B603 LOW findings in
#   ensure_tools.py only (no MEDIUM/HIGH, no other files -- enforced by
#   tests/unit/test_bandit_baseline.py).
#   [tool.bandit].skips in pyproject.toml remains empty -- no global suppression, and
#   no in-code # nosec annotations are used. The baseline is regenerated (never
#   hand-edited) with: uv run bandit scripts/ensure_tools.py -f json -o .bandit-baseline.json
#
# Pass 2 (all other scripts, --severity-level medium):
#   All other scripts under scripts/ are scanned without the baseline.
#   --severity-level medium means bandit exits non-zero only on MEDIUM or HIGH findings;
#   pre-existing LOW findings (B404/B603/B607/B105/B101 in other scripts) are reported
#   but do not fail the gate, keeping the scanner live for new HIGH/MEDIUM issues.
py-security:
	uv run bandit scripts/ensure_tools.py -b .bandit-baseline.json
	uv run bandit -r scripts --exclude scripts/ensure_tools.py --severity-level medium

# Coverage threshold read from monorepo-config.json via PY_COVERAGE_MIN (ledger D22).
py-test:
	uv run pytest tests/unit --cov=scripts --cov-fail-under=$(PY_COVERAGE_MIN)

# Composite job target: all Python quality gates (consumed by the python-quality CI job).
python-quality: py-format-check py-lint py-typecheck py-security py-test

# ---------------------------------------------------------------------------
# Go quality gates
# ---------------------------------------------------------------------------

go-format:
	uv run python -m scripts.run_go fmt

go-lint:
	uv run python -m scripts.run_go lint

go-vuln:
	uv run python -m scripts.run_go vuln

# Coverage threshold read from monorepo-config.json via GO_COVERAGE_MIN (ledger D22).
go-unit-test-coverage:
	uv run python -m scripts.run_go unit-test-coverage --threshold $(GO_COVERAGE_MIN)

go-unit-test-coverage-json:
	uv run python -m scripts.run_go unit-test-coverage-json

# ---------------------------------------------------------------------------
# Rego / OPA quality gates
# ---------------------------------------------------------------------------

rego-format:
	uv run python -m scripts.run_opa fmt --list policies

rego-lint:
	uv run python -m scripts.run_opa check policies

# Coverage threshold read from monorepo-config.json via REGO_COVERAGE_MIN (ledger D22).
rego-unit-test-coverage:
	uv run python -m scripts.run_opa test policies --coverage --threshold $(REGO_COVERAGE_MIN)

rego-unit-test-coverage-json:
	uv run python -m scripts.run_opa test policies --coverage --format json

# ---------------------------------------------------------------------------
# Terraform module gates (parameterized by MODULE_PATH / MODULE_TYPE)
# ---------------------------------------------------------------------------

tf-format:
	uv run python -m scripts.run_terraform fmt --check --recursive $(MODULE_PATH)

# Repo-wide HCL format check (no MODULE_PATH; used by pre-commit hook, ledger D47).
tf-format-check:
	uv run python -m scripts.run_terraform fmt --check --recursive .

tf-lint:
	uv run python -m scripts.run_terraform lint --chdir $(MODULE_PATH)

tf-security:
	uv run python -m scripts.run_trivy config --exit-code 1 --ignorefile .trivyignore $(MODULE_PATH)

tf-docs-check:
	uv run python -m scripts.run_terraform_docs check $(MODULE_PATH)

module-validate:
	uv run python -m scripts.run_opa module-validate --module-path $(MODULE_PATH) --module-type $(MODULE_TYPE)

tf-plan:
	uv run python -m scripts.run_terraform plan $(MODULE_PATH)

tf-test:
	uv run python -m scripts.run_terratest $(MODULE_PATH)

tf-test-all:
	uv run python -m scripts.run_terratest --all

tf-validate:
	uv run python -m scripts.run_terraform validate $(MODULE_PATH)

# ---------------------------------------------------------------------------
# Terragrunt gates
# ---------------------------------------------------------------------------

tg-format-check:
	uv run python -m scripts.run_terragrunt hcl-format-check

tg-validate:
	uv run python -m scripts.run_terragrunt validate $(INCLUDE_DIR_FLAGS)

tg-security:
	uv run python -m scripts.run_trivy config --exit-code 1 --ignorefile .trivyignore terragrunt/

tg-plan:
	uv run python -m scripts.run_terragrunt plan $(INCLUDE_DIR_FLAGS)

tg-apply:
	uv run python -m scripts.run_terragrunt apply $(INCLUDE_DIR_FLAGS)

# Ephemeral teardown lane (sandbox perf-test workflow). Change-scoped via INCLUDE_DIR_FLAGS,
# exactly like tg-apply, so it destroys precisely the stand-up scope. NOT part of any push/PR
# lane -- invoked only by a deliberate on-demand run.
tg-destroy:
	uv run python -m scripts.run_terragrunt destroy $(INCLUDE_DIR_FLAGS)

tg-detect-units:
	uv run python -m scripts.detect_terragrunt_units --base $(BASE) --head $(HEAD) --terragrunt-root terragrunt/ --output $(OUTPUT)

tg-detect-apply-units:
	uv run python -m scripts.detect_terragrunt_units --base $(BASE) --head $(HEAD) --terragrunt-root terragrunt/ --output $(OUTPUT) --exclude-bootstrap

tg-filter-deployable-units:
	uv run python -m scripts.filter_ci_deployable_units --include-dir-flags "$(INCLUDE_DIR_FLAGS)" --accounts-json terragrunt/common/accounts.json --output $(OUTPUT)

tg-filter-deployed-units:
	uv run python -m scripts.filter_units_with_state --include-dir-flags "$(INCLUDE_DIR_FLAGS)" --terragrunt-root terragrunt/ --output $(OUTPUT)

tg-resolve-deploy-role:
	uv run python -m scripts.resolve_deploy_role --include-dir-flags "$(INCLUDE_DIR_FLAGS)" --accounts-json terragrunt/common/accounts.json --output $(OUTPUT)

tg-partition-units:
	uv run python -m scripts.partition_units_by_account --include-dir-flags "$(INCLUDE_DIR_FLAGS)" --accounts-json terragrunt/common/accounts.json --mode $(MODE) --output $(OUTPUT)

# Select ONE account's row (role_arn/aws_region/include_dir_flags) from a partition matrix file.
# Used by the ephemeral perf-test lane to scope the stand-up/tear-down to the sandbox SERVICE
# account only (dns-owner units excluded by account, never by a hardcoded path list).
tg-select-partition-row:
	uv run python -m scripts.select_partition_row --matrix-file $(MATRIX_FILE) --account-id $(ACCOUNT_ID) --output $(OUTPUT)

tg-output:
	uv run python -m scripts.tg_output --unit $(UNIT) --name $(NAME) --output $(OUTPUT)

tg-regression:
	uv run python -m scripts.tg_regression

tf-state-preflight:
	uv run python -m scripts.tf_state_preflight

tf-validate-dependency-paths:
	uv run python -m scripts.tf_validate_dependency_paths

tf-guard-pinned-sources:
	uv run python -m scripts.tf_guard_pinned_sources

tf-guard-const-sources:
	uv run python -m scripts.tf_guard_const_sources --root providers/aws/references

tf-guard-version-floor:
	uv run python -m scripts.tf_guard_version_floor

tg-bucket-name-unique:
	uv run python -m scripts.tg_bucket_name_unique

tg-no-hardcoded-identity:
	uv run python -m scripts.tg_no_hardcoded_identity --root terragrunt/live

tg-copyability-test:
	uv run python -m scripts.tg_copyability_test --level all

# ---------------------------------------------------------------------------
# Terratest quality gates
# ---------------------------------------------------------------------------

# FR-3 static tagging-contract lint (spec section 4.3, ledger D11).
# Asserts default_tags wiring, no-default variable declarations, and committed
# offline tfvars values across providers/aws/{primitives,references}/*/examples/*/
terratest-tags-check:
	uv run python -m scripts.check_terratest_tags

# FR-19 item-1 per-module per-example first-party coverage gate (spec section 4.19, D-25).
# Asserts every examples/<ex>/ directory has at least one first-party apply-level test
# (vendored copies under **/.terraform/** excluded) across providers/aws/{primitives,references}/
terratest-coverage-check:
	uv run python -m scripts.check_terratest_coverage

# ---------------------------------------------------------------------------
# YAML quality gates
# ---------------------------------------------------------------------------

yaml-format-check:
	uv run yamlfix --check --exclude "apps/*/node_modules/**" --exclude ".venv/**" --exclude "**/.terraform/**" --exclude "**/.terragrunt-cache/**" --exclude ".terraform-docs.yml" --exclude ".github/workflows/main-validation.yml" --exclude ".github/workflows/safety-net.yml" .

yaml-lint:
	uv run yamllint .

actionlint:
	uv run python -m scripts.run_actionlint

actions-sha-pin-check:
	uv run python -m scripts.check_action_sha_pins

# ---------------------------------------------------------------------------
# Markdown quality gate
# ---------------------------------------------------------------------------

md-lint:
	uv run pymarkdown --config .pymarkdown.yml scan .

# ---------------------------------------------------------------------------
# Scope / release / orchestration targets (doc-03 section 2.7)
# ---------------------------------------------------------------------------

scope-detect:
	uv run python -m scripts.ci_detect_scope --config monorepo-config.json --scope-override $(SCOPE_OVERRIDE) --diff-range "$(DIFF_RANGE)" --output $(OUTPUT)

# Guard against a module required-variable added without terragrunt leaf wiring (#235).
# A module-only PR that adds a new REQUIRED (no default) variable to a reference/
# primitive module does not trigger the terragrunt plan lane, so an unwired required
# input would only break `terragrunt plan`/`apply` post-merge. This guard detects that
# precise, fail-closed pattern from the PR diff. DIFF_RANGE is caller-supplied (CI passes
# 'origin/main...HEAD'); when unset the script defaults to origin/main...HEAD.
check-required-var-wiring:
	uv run python -m scripts.check_required_var_wiring --config monorepo-config.json $(if $(DIFF_RANGE),--diff-range "$(DIFF_RANGE)",)

resolve-module-type:
	uv run python -m scripts.resolve_module_type --config monorepo-config.json --module-path $(MODULE_PATH) --output $(OUTPUT)

validate-pr-title:
	uv run python -m scripts.validate_pr_title --title "$(PR_TITLE)"

calculate-version:
	uv run python -m scripts.ci_calculate_version

# Repo-wide root-VERSION immutability guard (B13). SCOPE defaults to 'config'
# (the single root VERSION file maps to config_tag_prefix in monorepo-config.json).
# Pass SCOPE=module MODULE_PATH=<dir> to guard a per-module VERSION instead.
check-version-immutability:
	uv run python -m scripts.check_version_immutability --base-ref $(BASE_REF) --scope $(if $(SCOPE),$(SCOPE),config) $(if $(MODULE_PATH),--module-path $(MODULE_PATH),)

simulate-merge:
	uv run python -m scripts.simulate_merge

check-staleness:
	uv run python -m scripts.check_staleness

update-version:
	uv run python -m scripts.update_version_files --scope $(SCOPE) $(if $(MODULE_PATH),--module-path $(MODULE_PATH),) --version $(VERSION) --repo-root $(REPO_ROOT)

generate-changelog:
	uv run python -m scripts.ci_generate_changelog --version $(VERSION) --bump-type $(BUMP_TYPE) --pr-title "$${PR_TITLE}" --output $(OUTPUT) $(if $(MODULE_PATH),--module-path $(MODULE_PATH),) $(if $(CHANGELOG_DIR),--changelog-dir $(CHANGELOG_DIR),)

lock-branch:
	uv run python -m scripts.lock_branch --action $(ACTION) --repo $(REPO) --branch $(BRANCH)

safety-net-unlock:
	uv run python -m scripts.safety_net_unlock --repo $(REPO) --branch $(BRANCH)

check-release-commit:
	uv run python -m scripts.check_release_commit --commit-message "$${COMMIT_MESSAGE}" --actor "$(ACTOR)" --output $(OUTPUT)

check-scope-override:
	uv run python -m scripts.check_scope_override --labels "$${LABELS}" --author "$(AUTHOR)" --org "$(ORG)" --output $(OUTPUT)

# Push (post-merge) scope-override resolution. The PR payload is unavailable on a push,
# so resolve the merged PR's detect-scope-override label + author from the commit SHA via
# the GitHub API and apply the same fail-closed admin authorization as check-scope-override.
check-merged-pr-override:
	uv run python -m scripts.check_merged_pr_override --commit-sha "$(COMMIT_SHA)" --repo "$(REPO)" --org "$(ORG)" --output $(OUTPUT)

# Merge-queue (merge_group) scope-override resolution. The merge_group event payload carries
# no labels/author, only the head ref whose embedded PR number identifies the queued PR; this
# resolves that PR via the GitHub API and applies the same fail-closed admin authorization,
# so a scope-override PR is not falsely rejected as multi-module during its queue validation.
check-mq-scope-override:
	uv run python -m scripts.check_merged_pr_override --merge-group-ref "$(HEAD_REF)" --repo "$(REPO)" --org "$(ORG)" --output $(OUTPUT)

git-fetch:
	uv run python -m scripts.release_steps fetch --remote $(REMOTE) --branch $(BRANCH)

git-reset-hard:
	uv run python -m scripts.release_steps reset --ref $(REF)

git-identity:
	uv run python -m scripts.release_steps identity --user "$(USER)" --email "$(EMAIL)"

publish-release:
	uv run python -m scripts.release_steps publish --tag-prefix $(TAG_PREFIX) --version $(VERSION) --full-tag $(FULL_TAG) --changelog-path $(CHANGELOG_PATH)

required-checks-aggregate:
	uv run python -m scripts.aggregate_required_checks --results "$${RESULTS}"

detect-module-changes:
	uv run python -m scripts.ci_detect_scope --config monorepo-config.json --scope-override false --output /dev/stdout

terratest-sweep:
	uv run python -m scripts.terratest_sweep --mode $(SWEEP_MODE) $(if $(SWEEP_RUN_ID),--run-id $(SWEEP_RUN_ID),)

# Parallel-safe per-module zero-orphan proof / cleanup. Reads the run id that
# run_terratest.py wrote to $(MODULE_PATH)/.terratest-run-id and runs a
# run-id-scoped (not account-wide) sweep, so concurrent module terratests
# never cross-contaminate each other's orphan check or delete each other's
# in-flight resources. The script fails fast with a clear ERROR when the
# run-id file is absent (the tf-test run never produced it). SWEEP_MODE
# selects check (inventory only, default) or delete (clean up THIS run's
# residue). All logic lives in the script (ledger D11), not the recipe.
terratest-sweep-module:
	uv run python -m scripts.terratest_sweep --mode $(if $(SWEEP_MODE),$(SWEEP_MODE),check) --run-id-file $(MODULE_PATH)/.terratest-run-id

# ---------------------------------------------------------------------------
# Git history check (FR-6, spec section 4.6, AC-14)
# ---------------------------------------------------------------------------

# Walk every blob reachable from all refs and assert none exceeds 100 MB.
# Satisfies AC-14: no blob anywhere in the branch history exceeds 100,000,000 bytes.
git-history-check:
	uv run python -m scripts.check_git_history --max-blob-bytes 100000000

# ---------------------------------------------------------------------------
# Live verification (FR-13, spec section 4.13, AC-30)
# ---------------------------------------------------------------------------

# Read-only prober: zero mutating AWS calls.
# CHECK must be one of: oidc-provider, oidc-roles, state-backend, stack,
#   endpoints, observability, no-project-resources, repo-settings
# ENV must be one of: sandbox, qa, prod, root
live-verify:
	uv run python -m scripts.live_verify --check $(CHECK) --env $(ENV)

# ---------------------------------------------------------------------------
# OIDC provider bootstrap (FR-10, spec section 4.10)
# ---------------------------------------------------------------------------

# Verify-first, idempotent: creates token.actions.githubusercontent.com OIDC provider
# (audience sts.amazonaws.com) in the target account only when absent.
# ENV must be qa or prod. sandbox (D33) and root (D-11) are refused.
# AWS credentials resolved via boto3.Session(profile_name=aws_profile) from
# terragrunt/common/accounts.json. No ambient AWS_PROFILE is used.
bootstrap-oidc-provider:
	uv run python -m scripts.bootstrap_oidc_provider --env $(ENV)

# ---------------------------------------------------------------------------
# OTLP end-to-end test harness (scripts/otlp_e2e_loadgen.py, e2e_verify.py).
# See README "OTLP end-to-end test harness". ENV is one of sandbox, prod, qa.
# ---------------------------------------------------------------------------

# Manifest + evidence paths are overridable; defaults are env-scoped so a run is
# self-contained. E2E_LOADGEN_ARGS forwards extra flags (e.g. --resolve, --count,
# --concurrency, --encoding) without editing the recipe.
E2E_MANIFEST ?= e2e-manifest-$(ENV).json
E2E_EVIDENCE ?= e2e-evidence
MODE ?= happy

# Generate OTLP/HTTP load + conformance traffic and write the sent-manifest.
e2e-loadgen:
	uv run python -m scripts.otlp_e2e_loadgen --env $(ENV) --mode $(MODE) --manifest-out $(E2E_MANIFEST) $(E2E_LOADGEN_ARGS)

# Verify the consumer pipeline (CWL/Firehose/S3/Glue/Athena/QuickSight) against a manifest.
# E2E_VERIFY_ARGS forwards extra flags (e.g. --cleanup, which deletes this run's synthetic
# tool=e2e-smoke objects after reconciliation) without editing the recipe.
e2e-verify:
	uv run python -m scripts.e2e_verify --env $(ENV) --manifest $(E2E_MANIFEST) --output $(E2E_EVIDENCE)/verify-$(ENV).json $(E2E_VERIFY_ARGS)

# Core data-plane round trip: generate load, then verify it flowed end to end.
# NOTE: e2e-loadgen POSTs to the public OTLP collector endpoint fronted by the WAF.
# The WAF permits any non-known-malicious source IP (the blanket anonymous-IP block
# was removed), so e2e-all runs from CI runner IPs as well as developer/operator
# networks -- the post-deploy gate runs it with --ambient-credentials (OIDC role).
# Set MODE=structured-events to instead drive the STRUCTURED Claude-style route (the
# second ADOT logs pipeline + cwl_split reshape); the default MODE=happy drives the
# flat body-contract path. e2e-queryability remains the emit-free, Athena-only guard.
e2e-all: e2e-loadgen e2e-verify

# Post-deploy queryability gate (CI-safe): assert the telemetry Glue table is
# enum-projected and queryable with NO tool filter (a bare SELECT count(*) SUCCEEDS)
# using the ambient/OIDC credential chain. It never emits through the WAF-fronted
# collector, so unlike e2e-all it runs from CI runner IPs. This is the regression
# guard for the enum tool partition-projection fix (an injected column rejected
# such queries with CONSTRAINT_VIOLATION).
e2e-queryability:
	uv run python -m scripts.e2e_queryability_check --env $(ENV) --output $(E2E_EVIDENCE)/queryability-$(ENV).json

# ---------------------------------------------------------------------------
# Telemetry-collector performance test (scripts/perf_loadgen.py,
# scripts/perf_report.py). ENV is one of sandbox, prod, qa. A heavier-load,
# longer-running sibling of the OTLP e2e harness above: perf-loadgen
# simulates many concurrent Claude Code sessions (reusing the harness's
# Claude-shaped OTLP payload builders and tool=e2e-smoke reconcile
# convention) instead of a small conformance batch, and perf-report pulls
# CloudWatch alongside the client-side results to render one graphical
# report. Results/report/evidence paths are overridable; defaults are
# env-scoped under the same $(E2E_EVIDENCE) directory the e2e harness uses.
# ---------------------------------------------------------------------------

PERF_RESULTS ?= $(E2E_EVIDENCE)/perf-results-$(ENV).json
PERF_REPORT_HTML ?= $(E2E_EVIDENCE)/perf-report-$(ENV).html
PERF_REPORT_EVIDENCE ?= $(E2E_EVIDENCE)/perf-evidence-$(ENV).json

# Run the virtual-user load driver and write the client-side results JSON.
# PERF_LOADGEN_ARGS forwards extra flags (e.g. --sessions, --ramp-seconds,
# --hold-seconds, --max-inflight, --resolve) without editing the recipe.
perf-loadgen:
	uv run python -m scripts.perf_loadgen --env $(ENV) --results-out $(PERF_RESULTS) $(PERF_LOADGEN_ARGS)

# Pull CloudWatch + the perf-loadgen results over the run window and render
# one self-contained graphical HTML report plus a JSON evidence summary.
# PERF_REPORT_ARGS forwards extra flags (e.g. --threshold-error-rate,
# --ambient-credentials, --ecs-cluster-name) without editing the recipe.
perf-report:
	uv run python -m scripts.perf_report --env $(ENV) --results $(PERF_RESULTS) --output $(PERF_REPORT_HTML) --evidence-out $(PERF_REPORT_EVIDENCE) $(PERF_REPORT_ARGS)

# Merge N per-shard perf_loadgen result JSONs into one combined results file for perf-report.
# PERF_MERGE_INPUTS is the caller-supplied list of --results <path> flags (one per shard).
perf-merge-results:
	uv run python -m scripts.perf_merge_results $(PERF_MERGE_INPUTS) --output $(PERF_RESULTS)

# Single-request collector reachability gate for the perf-test lane. Polls a minimal valid
# OTLP-logs request against the collector's CloudFront default-domain endpoint (PERF_ENDPOINT,
# a full https:// URL) until it answers at the HTTP layer, then the load run proceeds. Bounded,
# env-driven timeout (PERF_READINESS_TIMEOUT / PERF_READINESS_POLL_INTERVAL); fail-fast on
# timeout. PERF_READINESS_ARGS forwards extra flags (e.g. --timeout, --output).
perf-readiness:
	uv run python -m scripts.perf_readiness --endpoint "$(PERF_ENDPOINT)" $(PERF_READINESS_ARGS)

# Resolve the collector CloudFront default-domain endpoint via the AWS API (bypasses
# both the non-resolving pretty FQDN and terragrunt output's cross-account dependency
# resolution). Appends collector_endpoint=<url> to OUTPUT ($GITHUB_OUTPUT).
perf-collector-endpoint:
	uv run python -m scripts.perf_collector_endpoint --env $(ENV) --output $(OUTPUT)

# Ephemeral perf-test teardown helper: recursively delete the env's namespace-discovered
# analytics Athena workgroup(s) so `terragrunt destroy` can no-op them (the athena-workgroup
# primitive exposes no force_destroy, so a workgroup with query history blocks the destroy).
# ATHENA_CLEANUP_ARGS forwards extra flags (e.g. --ambient-credentials, --aws-region, --output).
athena-workgroup-cleanup:
	uv run python -m scripts.athena_workgroup_cleanup --env $(ENV) $(ATHENA_CLEANUP_ARGS)

# ---------------------------------------------------------------------------
# Ephemeral perf-test WAF allowlist (scripts/perf_waf_allowlist.py). The collector
# CloudFront WAFv2 WebACL enforces a per-source-IP rate cap (rate_limit_per_ip, D44);
# a heavy single-origin load test trips it. These targets add a terminating ALLOW rule
# + IPSet (CLOUDFRONT scope, us-east-1) that whitelists the load-runner egress CIDR(s) so
# perf traffic bypasses the cap, and REMOVE both afterward. Idempotent + optimistic-lock
# safe (parallel load shards each merge their own IP). PERF_WAF_ARGS forwards extra flags
# (e.g. --web-acl-name, --add-cidr, --allow-rule-priority, --lock-retries). Ambient/OIDC
# credentials via --ambient-credentials.
# ---------------------------------------------------------------------------
perf-waf-allowlist-apply:
	uv run python -m scripts.perf_waf_allowlist apply --env $(ENV) $(PERF_WAF_ARGS)

perf-waf-allowlist-remove:
	uv run python -m scripts.perf_waf_allowlist remove --env $(ENV) $(PERF_WAF_ARGS)

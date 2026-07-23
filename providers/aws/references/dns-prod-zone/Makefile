# Dependency resolution for this module is owned by mpm
# (https://github.com/matthew-dresden/mpm). Declarations live in the repo-root
# `.mpm` manifest, which mpm auto-discovers by walking up from the current
# directory, and resolved SHAs are pinned in `.mpm.lock`. There is no
# per-module config file, so this Makefile never fails at parse time; the CLI
# is checked at recipe time by the `mpm-check` target.
MPM ?= mpm
MPM_INSTALL_HINT = pipx install git+https://github.com/matthew-dresden/mpm@v1.0.0

# `mpm install` aggregates every synced package into `.packages/` next to the
# `.mpm` manifest at the repo root. Resolve that path here so a module Makefile
# can include package-provided Makefiles regardless of its own depth.
MPM_ROOT ?= $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
PACKAGES_DIR ?= $(MPM_ROOT)/.packages

# Include project level defaults (env vars, etc.)
-include Makefile.includes

# Include all package Makefiles after configuration is loaded
-include $(PACKAGES_DIR)/*/Makefile

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available make targets
	@echo "Available targets:"
	@$(MAKE) -pRrq -f $(firstword $(MAKEFILE_LIST)) : 2>/dev/null | \
		awk -v RS= -F: '/^# File/,/^# Finished Make data base/ {if ($$1 !~ "^[#.]") {print $$1}}' | \
		sort | uniq | \
		grep -v -e '^[^[:alnum:]]' -e '^$@$$'

.PHONY: tf-format
tf-format: ## Run terraform fmt check
	uv run python -c "import subprocess, sys; result = subprocess.run(['terraform', 'fmt', '-recursive', '-check', '.'], capture_output=False); sys.exit(result.returncode)"

.PHONY: tf-lint
tf-lint: ## Run tflint
	uv run python -c "import subprocess, sys; result = subprocess.run(['tflint', '--recursive'], capture_output=False); sys.exit(result.returncode)"

.PHONY: tf-security
tf-security: ## Run trivy security scan
	uv run python -c "import subprocess, sys; result = subprocess.run(['trivy', 'config', '--scanners', 'misconfig', '.'], capture_output=False); sys.exit(result.returncode)"

.PHONY: tf-validate
tf-validate: ## Run terraform validate on all examples
	@for dir in examples/*/; do \
		echo "Validating $$dir"; \
		uv run python -c "import subprocess, sys, os; os.chdir('$$dir'); r1 = subprocess.run(['terraform', 'init', '-backend=false'], capture_output=False); r2 = subprocess.run(['terraform', 'validate'], capture_output=False); sys.exit(r1.returncode or r2.returncode)"; \
	done

.PHONY: tf-docs-check
tf-docs-check: ## Check terraform-docs drift
	uv run python -c "import subprocess, sys; result = subprocess.run(['terraform-docs', 'markdown', 'table', '--output-file', 'README.md', '--output-mode', 'inject', '.'], capture_output=False); sys.exit(result.returncode)"

.PHONY: module-validate
module-validate: ## Run OPA module validation
	uv run python -c "import subprocess, sys; result = subprocess.run(['opa', 'eval', '-d', 'policies/', '-i', '.', 'data.module.allow'], capture_output=False); sys.exit(result.returncode)"

.PHONY: tf-test
tf-test: ## Run Terratest functional tests
	uv run python -c "import subprocess, sys; result = subprocess.run(['go', 'test', '-v', '-timeout', '30m', './tests/...'], capture_output=False); sys.exit(result.returncode)"

.PHONY: validate
validate: tf-format tf-lint tf-security tf-validate tf-docs-check module-validate ## Run all quality gates

.PHONY: mpm-check
mpm-check: ## Verify the mpm CLI is installed
	@command -v $(MPM) >/dev/null 2>&1 || { \
		echo "Error: '$(MPM)' not found on PATH. Install it with: $(MPM_INSTALL_HINT)"; \
		exit 1; \
	}

.PHONY: mpm-install
mpm-install: mpm-check ## Sync declared mpm sources into $(PACKAGES_DIR) and write .mpm.lock
	$(MPM) install

.PHONY: mpm-clean
mpm-clean: mpm-check ## Remove this project's synced packages and mpm state
	$(MPM) clean

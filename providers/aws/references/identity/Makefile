CPM_ENV_FILE = .cpmenv
ifeq (,$(wildcard $(CPM_ENV_FILE)))
$(error Error: $(CPM_ENV_FILE) not found. Run: curl -o .cpmenv https://raw.githubusercontent.com/caylent-solutions/cpm/main/examples/example-make-task-runner/.cpmenv)
endif
include $(CPM_ENV_FILE)

# Validate required variables
REQUIRED_VARS := \
	REPO_MANIFESTS_URL \
	REPO_MANIFESTS_REVISION \
	REPO_MANIFESTS_PATH \
	REPO_URL \
	REPO_REV \
	ASDF_URL \
	ASDF_VERSION \
	ASDF_DIR \
	TOOL_VERSIONS_FILE \
	DEFAULT_PYTHON_VERSION \
	PACKAGES_DIR
$(foreach var,$(REQUIRED_VARS),$(if $($(var)),,$(error Error: $(var) not set in $(CPM_ENV_FILE))))

# Export variables for repo tool
export REPO_URL REPO_REV GITBASE

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
	uv run python -c "import subprocess, sys; result = subprocess.run(['trivy', 'config', '.'], capture_output=False); sys.exit(result.returncode)"

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

.PHONY: cpm-clean
cpm-clean:
	@rm -rf $(PACKAGES_DIR) .repo
	@echo "Cleaned $(PACKAGES_DIR) and .repo directories"

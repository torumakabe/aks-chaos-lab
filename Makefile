SHELL := /bin/bash

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Tool images / versions (kept in sync with .github/workflows/ci.yml)
ACTIONLINT_IMAGE := rhysd/actionlint:1.7.12
KUBECONFORM_IMAGE := ghcr.io/yannh/kubeconform:v0.7.0
K8S_VERSION := 1.33.0
KUBECONFORM_SKIP := VerticalPodAutoscaler,CiliumNetworkPolicy,Kustomization,Gateway,HTTPRoute,Instrumentation

.PHONY: help qa qa-app qa-platform qa-workflows \
        lint-workflows compile-aw lint-bicep lint-k8s \
        install-tools check-docker check-az check-gh-aw \
        clean

help: ## Show help
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

qa: qa-workflows qa-platform qa-app ## Run all QA (workflows + platform + app)
	@echo -e "$(GREEN)✓ All QA passed$(NC)"

qa-app: ## Run application QA (delegates to src/Makefile)
	@echo -e "$(YELLOW)→ Application QA...$(NC)"
	@$(MAKE) -C src qa

qa-platform: lint-bicep lint-k8s ## Run platform QA (bicep + k8s)
	@echo -e "$(GREEN)✓ Platform QA passed$(NC)"

qa-workflows: lint-workflows compile-aw ## Run workflows QA (actionlint + gh-aw compile)
	@echo -e "$(GREEN)✓ Workflows QA passed$(NC)"

lint-workflows: check-docker ## Lint GitHub Actions workflows with actionlint (Docker)
	@echo -e "$(YELLOW)→ actionlint on .github/workflows/...$(NC)"
	@docker run --rm -v "$(CURDIR):/repo" -w /repo $(ACTIONLINT_IMAGE) -color
	@echo -e "$(GREEN)✓ actionlint passed$(NC)"

compile-aw: check-gh-aw ## Verify gh-aw agentic workflows compile cleanly (no drift in lock.yml)
	@echo -e "$(YELLOW)→ gh aw compile...$(NC)"
	@gh aw compile
	@if ! git diff --quiet -- .github/workflows/*.lock.yml; then \
		echo -e "$(RED)✗ gh-aw lock.yml is out of date. Commit the regenerated file.$(NC)"; \
		git --no-pager diff --stat -- .github/workflows/*.lock.yml; \
		exit 1; \
	fi
	@echo -e "$(GREEN)✓ gh-aw compile is clean$(NC)"

lint-bicep: check-az ## Build Bicep templates (infra/main.bicep)
	@echo -e "$(YELLOW)→ az bicep build infra/main.bicep...$(NC)"
	@az bicep build --file infra/main.bicep
	@echo -e "$(GREEN)✓ Bicep build passed$(NC)"

lint-k8s: check-docker ## Validate Kubernetes manifests with kubeconform (Docker)
	@echo -e "$(YELLOW)→ kubeconform on k8s/base/...$(NC)"
	@docker run --rm -v "$(CURDIR):/repo" -w /repo --entrypoint /kubeconform $(KUBECONFORM_IMAGE) \
		-strict -summary \
		-kubernetes-version $(K8S_VERSION) \
		-skip $(KUBECONFORM_SKIP) \
		k8s/base/*.yaml
	@echo -e "$(GREEN)✓ kubeconform passed$(NC)"

install-tools: ## Check required tools and print install hints
	@echo -e "$(YELLOW)Checking required tools...$(NC)"
	@$(MAKE) -s check-docker check-az check-gh-aw
	@echo -e "$(GREEN)✓ All required tools available$(NC)"

check-docker:
	@command -v docker >/dev/null 2>&1 || { \
		echo -e "$(RED)✗ docker not found$(NC)"; \
		echo "  Install: https://docs.docker.com/get-docker/"; \
		exit 1; \
	}

check-az:
	@command -v az >/dev/null 2>&1 || { \
		echo -e "$(RED)✗ az (Azure CLI) not found$(NC)"; \
		echo "  Install: https://learn.microsoft.com/cli/azure/install-azure-cli"; \
		exit 1; \
	}

check-gh-aw:
	@command -v gh >/dev/null 2>&1 || { \
		echo -e "$(RED)✗ gh (GitHub CLI) not found$(NC)"; \
		echo "  Install: https://cli.github.com/"; \
		exit 1; \
	}
	@gh aw --version >/dev/null 2>&1 || { \
		echo -e "$(RED)✗ gh-aw extension not installed$(NC)"; \
		echo "  Install: gh extension install github/gh-aw"; \
		exit 1; \
	}

clean: ## Clean caches (delegates to src/Makefile)
	@$(MAKE) -C src clean

SHELL := /bin/bash

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

.PHONY: help install test test-cov test-integration test-load test-all lint typecheck format format-check check clean build run load-smoke load-baseline load-stress load-spike sync sync-dev qa check-uv-version

help: ## Show help
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install development dependencies
	@echo -e "$(YELLOW)Installing development dependencies...$(NC)"
	@uv sync --all-groups
	@echo -e "$(GREEN)✓ Dependencies installed$(NC)"

sync: ## uv sync (default)
	@echo -e "$(YELLOW)Syncing dependencies...$(NC)"
	@uv sync
	@echo -e "$(GREEN)✓ Dependencies synced$(NC)"


sync-dev: ## uv sync for dev group
	@echo -e "$(YELLOW)Syncing dev dependencies...$(NC)"
	@uv sync --group dev
	@echo -e "$(GREEN)✓ Dev dependencies synced$(NC)"

test: ## Run unit tests
	@echo -e "$(YELLOW)Running unit tests...$(NC)"
	@PYTHONPATH=. uv run pytest tests/unit/ -q
	@echo -e "$(GREEN)✓ Unit tests passed$(NC)"

test-cov: ## Run unit tests with coverage report
	@echo -e "$(YELLOW)Running unit tests with coverage...$(NC)"
	@PYTHONPATH=. uv run pytest tests/unit/ --cov=app --cov-report=term-missing --cov-report=html
	@echo -e "$(GREEN)✓ Coverage report generated (see htmlcov/index.html)$(NC)"

# Optional integration tests hook (exists in our tree)
.PHONY: test-integration
test-integration: ## Run integration tests
	PYTHONPATH=. uv run pytest tests/integration/ -q

.PHONY: test-load
test-load: ## Run load tests (smoke scenario; quick)
	cd tests/load && ./run-load-tests.sh smoke

.PHONY: test-all
test-all: test test-integration ## Run all tests

lint: ## Run linting
	@echo -e "$(YELLOW)Running linter...$(NC)"
	@uv run ruff check app/ --fix
	@echo -e "$(GREEN)✓ Linting complete$(NC)"

typecheck: ## Run type checking
	@echo -e "$(YELLOW)Running type checker...$(NC)"
	@uv run pyright app/
	@echo -e "$(GREEN)✓ Type checking complete$(NC)"

format: ## Format code
	@echo -e "$(YELLOW)Formatting code...$(NC)"
	@uv run ruff format app/
	@echo -e "$(GREEN)✓ Code formatted$(NC)"

format-check: ## Check code formatting (no changes)
	@echo -e "$(YELLOW)Checking code format...$(NC)"
	@uv run ruff format app/ --check
	@echo -e "$(GREEN)✓ Formatting check passed$(NC)"

check: lint typecheck test ## Run all checks (lint, typecheck, test)

clean: ## Clean caches
	@echo -e "$(YELLOW)Cleaning caches...$(NC)"
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache htmlcov .coverage 2>/dev/null || true
	@echo -e "$(GREEN)✓ Caches cleaned$(NC)"

build: ## Build Docker image locally
	@echo -e "$(YELLOW)Building Docker image...$(NC)"
	@docker build -f Dockerfile -t aks-chaos-lab:local .
	@echo -e "$(GREEN)✓ Docker image built$(NC)"

run: ## Run app locally
	@echo -e "$(YELLOW)Starting app on http://localhost:8000...$(NC)"
	@PYTHONPATH=. uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Convenience targets mirroring ACA load profiles
load-baseline: ## Run locust baseline profile
	cd tests/load && uv run ./run-load-tests.sh baseline

load-stress: ## Run locust stress profile
	cd tests/load && uv run ./run-load-tests.sh stress

load-spike: ## Run locust spike profile
	cd tests/load && uv run ./run-load-tests.sh spike

load-smoke: ## Run locust smoke profile (quick)
	cd tests/load && uv run ./run-load-tests.sh smoke

qa: ## Lint + Test + Typecheck (quick)
	@echo -e "$(YELLOW)Running QA checks...$(NC)"
	@echo -e "$(YELLOW)  → Format check...$(NC)"
	@PYTHONPATH=. uv run ruff format app/ --check
	@echo -e "$(YELLOW)  → Linting...$(NC)"
	@PYTHONPATH=. uv run ruff check app --fix
	@echo -e "$(YELLOW)  → Testing...$(NC)"
	@PYTHONPATH=. uv run pytest -q
	@echo -e "$(YELLOW)  → Type checking...$(NC)"
	@PYTHONPATH=. uv run pyright app
	@echo -e "$(GREEN)✓ All QA checks passed$(NC)"

check-uv-version: ## Check uv version consistency (local vs Docker)
	@echo -e "$(YELLOW)Checking uv version consistency...$(NC)"
	@expected=$$(grep -oP 'required-version\s*=\s*">=\K[0-9]+\.[0-9]+\.[0-9]+' pyproject.toml 2>/dev/null || echo "not set"); \
	local_ver=$$(uv --version 2>/dev/null | awk '{print $$2}' || echo "not installed"); \
	docker_ver=$$(grep -oP 'ghcr\.io/astral-sh/uv:\K[0-9]+\.[0-9]+\.[0-9]+' Dockerfile 2>/dev/null | head -1 || echo "not found"); \
	echo "  Expected (pyproject.toml required-version): $$expected"; \
	echo "  Local uv:                                   $$local_ver"; \
	echo "  Docker uv:                                  $$docker_ver"; \
	if [ "$$docker_ver" != "$$expected" ]; then \
		echo -e "$(RED)✗ Docker uv version mismatch with pyproject.toml$(NC)"; \
		exit 1; \
	fi; \
	if [ "$$local_ver" != "$$expected" ]; then \
		echo -e "$(YELLOW)⚠ Warning: Local uv ($$local_ver) differs from expected ($$expected)$(NC)"; \
		echo "  Note: Patch version differences within same minor version are compatible"; \
		echo "  Lockfile compatibility is enforced by 'uv sync --locked' in Docker build"; \
	else \
		echo -e "$(GREEN)✓ All uv versions match$(NC)"; \
	fi

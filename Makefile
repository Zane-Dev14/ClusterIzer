# KubeSentinel Makefile

.PHONY: help install run test lint typecheck clean

help:  ## Show this help message
	@echo "KubeSentinel - Kubernetes Intelligence Engine"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies using uv
	uv sync

run:  ## Run KubeSentinel scan
	uv run kubesentinel scan

test:  ## Run tests
	uv run pytest kubesentinel/tests/ -v

lint:  ## Run ruff linter
	uv run ruff check kubesentinel/

typecheck:  ## Run mypy type checker
	uv run mypy kubesentinel/ --ignore-missing-imports

clean:  ## Clean generated files
	rm -f report.md
	rm -f infra_memory.db
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

.DEFAULT_GOAL := help

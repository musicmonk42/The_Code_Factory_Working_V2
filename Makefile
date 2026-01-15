# Code Factory Platform Makefile
# This file provides convenient commands for development, testing, and deployment

.PHONY: help install install-dev test lint format clean docker-build docker-up docker-down deploy-staging deploy-production

# Default target
.DEFAULT_GOAL := help

# Color output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)Code Factory Platform - Available Commands$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'

# =============================================================================
# Installation
# =============================================================================

install: ## Install all dependencies for unified platform (production)
	@echo "$(BLUE)Installing unified platform dependencies...$(NC)"
	pip install --upgrade pip setuptools wheel
	pip install -r requirements.txt
	@echo "$(GREEN)Installation complete!$(NC)"

install-dev: ## Install all dependencies including development tools
	@echo "$(BLUE)Installing unified platform development dependencies...$(NC)"
	pip install --upgrade pip setuptools wheel
	pip install -r requirements.txt
	pip install pytest pytest-cov pytest-asyncio pytest-mock black ruff flake8 mypy bandit safety pip-audit
	@echo "$(GREEN)Development installation complete!$(NC)"

# =============================================================================
# Testing
# =============================================================================

test: ## Run all tests
	@echo "$(BLUE)Running all tests...$(NC)"
	pytest -v --tb=short
	@echo "$(GREEN)Tests complete!$(NC)"

test-generator: ## Run Generator tests
	@echo "$(BLUE)Running Generator tests...$(NC)"
	cd generator && pytest tests/ -v --tb=short

test-omnicore: ## Run OmniCore Engine tests
	@echo "$(BLUE)Running OmniCore Engine tests...$(NC)"
	cd omnicore_engine && pytest tests/ -v --tb=short

test-sfe: ## Run Self-Fixing Engineer tests
	@echo "$(BLUE)Running Self-Fixing Engineer tests...$(NC)"
	cd self_fixing_engineer && pytest tests/ -v --tb=short

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	pytest --cov --cov-report=html --cov-report=term -v
	@echo "$(GREEN)Coverage report generated in htmlcov/$(NC)"

test-watch: ## Run tests in watch mode (requires pytest-watch)
	@echo "$(BLUE)Running tests in watch mode...$(NC)"
	pip install pytest-watch
	ptw -- -v

# =============================================================================
# Code Quality
# =============================================================================

lint: ## Run all linters on entire platform
	@echo "$(BLUE)Running linters on entire platform...$(NC)"
	@echo "$(YELLOW)Running Black...$(NC)"
	black --check generator/ omnicore_engine/ self_fixing_engineer/ *.py
	@echo "$(YELLOW)Running Ruff...$(NC)"
	ruff check generator/ omnicore_engine/ self_fixing_engineer/ *.py
	@echo "$(YELLOW)Running Flake8...$(NC)"
	flake8 generator/ omnicore_engine/ self_fixing_engineer/ *.py --count --select=E9,F63,F7,F82 --show-source --statistics
	@echo "$(GREEN)Linting complete!$(NC)"

format: ## Format code with Black
	@echo "$(BLUE)Formatting code...$(NC)"
	black generator/ omnicore_engine/ self_fixing_engineer/
	@echo "$(GREEN)Code formatted!$(NC)"

type-check: ## Run type checking with mypy
	@echo "$(BLUE)Running type checks...$(NC)"
	mypy generator/ omnicore_engine/ self_fixing_engineer/
	@echo "$(GREEN)Type checking complete!$(NC)"

security-scan: ## Run security scans
	@echo "$(BLUE)Running security scans...$(NC)"
	@echo "$(YELLOW)Running Bandit...$(NC)"
	bandit -r generator/ omnicore_engine/ self_fixing_engineer/
	@echo "$(YELLOW)Running Safety...$(NC)"
	safety check -r requirements.txt
	@echo "$(GREEN)Security scan complete!$(NC)"

# =============================================================================
# Docker
# =============================================================================

docker-build: ## Build all Docker images
	@echo "$(BLUE)Building Docker images...$(NC)"
	docker build -t code-factory:latest -f Dockerfile .
	docker build -t code-factory-generator:latest -f generator/Dockerfile ./generator
	@echo "$(GREEN)Docker images built!$(NC)"

docker-up: ## Start all services with Docker Compose
	@echo "$(BLUE)Starting Docker Compose services...$(NC)"
	docker compose up -d
	@echo "$(GREEN)Services started!$(NC)"
	@echo "$(YELLOW)Generator: http://localhost:8000$(NC)"
	@echo "$(YELLOW)OmniCore: http://localhost:8001$(NC)"
	@echo "$(YELLOW)Grafana: http://localhost:3000$(NC)"
	@echo "$(YELLOW)Prometheus: http://localhost:9090$(NC)"

docker-down: ## Stop all Docker Compose services
	@echo "$(BLUE)Stopping Docker Compose services...$(NC)"
	docker compose down
	@echo "$(GREEN)Services stopped!$(NC)"

docker-logs: ## Show Docker Compose logs
	docker compose logs -f

docker-clean: ## Remove all Docker containers, images, and volumes
	@echo "$(RED)Cleaning Docker resources...$(NC)"
	docker compose down -v
	docker system prune -af
	@echo "$(GREEN)Docker resources cleaned!$(NC)"

# =============================================================================
# Development
# =============================================================================

run-generator: ## Run Generator locally
	@echo "$(BLUE)Starting Generator...$(NC)"
	cd generator && python -m main.main --interface api

run-omnicore: ## Run OmniCore Engine locally
	@echo "$(BLUE)Starting OmniCore Engine...$(NC)"
	cd omnicore_engine && python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 --reload

run-cli: ## Run OmniCore CLI
	@echo "$(BLUE)Running OmniCore CLI...$(NC)"
	cd omnicore_engine && python -m omnicore_engine.cli --help

health-check: ## Run health check on all services
	@echo "$(BLUE)Running health checks...$(NC)"
	python health_check.py
	@echo "$(GREEN)Health check complete!$(NC)"

# =============================================================================
# Database
# =============================================================================

db-migrate: ## Run database migrations
	@echo "$(BLUE)Running database migrations...$(NC)"
	# Add migration commands here
	@echo "$(GREEN)Migrations complete!$(NC)"

db-reset: ## Reset database (WARNING: destroys data)
	@echo "$(RED)Resetting database...$(NC)"
	rm -f dev.db deploy_agent_history.db mock_history.db
	@echo "$(GREEN)Database reset!$(NC)"

# =============================================================================
# Deployment
# =============================================================================

deploy-staging: ## Deploy to staging environment
	@echo "$(BLUE)Deploying to staging...$(NC)"
	# Add staging deployment commands here
	@echo "$(GREEN)Deployed to staging!$(NC)"

deploy-production: ## Deploy to production environment
	@echo "$(RED)Deploying to production...$(NC)"
	# Add production deployment commands here
	@echo "$(GREEN)Deployed to production!$(NC)"

# =============================================================================
# Cleanup
# =============================================================================

clean: ## Clean up generated files and caches
	@echo "$(BLUE)Cleaning up...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type f -name "*~" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml
	rm -rf dist/ build/
	@echo "$(GREEN)Cleanup complete!$(NC)"

clean-all: clean docker-clean db-reset ## Deep clean (removes Docker resources and databases)
	@echo "$(GREEN)Deep cleanup complete!$(NC)"

clean-old-docs: ## Remove audit/test documents older than 2 days (interactive)
	@echo "$(BLUE)Cleaning old audit and test documents...$(NC)"
	python cleanup_old_docs.py --dry-run
	@echo "$(YELLOW)To actually delete files, run: make clean-old-docs-force$(NC)"

clean-old-docs-force: ## Remove audit/test documents older than 2 days (automatic)
	@echo "$(BLUE)Cleaning old audit and test documents...$(NC)"
	python cleanup_old_docs.py --yes
	@echo "$(GREEN)Old docs cleanup complete!$(NC)"

# =============================================================================
# Documentation
# =============================================================================

docs: ## Generate documentation
	@echo "$(BLUE)Generating documentation...$(NC)"
	# Add documentation generation commands here
	@echo "$(GREEN)Documentation generated!$(NC)"

docs-serve: ## Serve documentation locally
	@echo "$(BLUE)Serving documentation...$(NC)"
	# Add documentation server commands here
	@echo "$(YELLOW)Documentation available at http://localhost:8080$(NC)"

# =============================================================================
# Monitoring
# =============================================================================

logs-generator: ## Show Generator logs
	tail -f generator/logs/*.log

logs-omnicore: ## Show OmniCore logs
	tail -f omnicore_engine/logs/*.log

logs-sfe: ## Show Self-Fixing Engineer logs
	tail -f self_fixing_engineer/logs/*.log

metrics: ## Show current metrics
	@echo "$(BLUE)Current Metrics:$(NC)"
	curl -s http://localhost:8001/metrics | head -20

# =============================================================================
# CI/CD Local Testing
# =============================================================================

ci-local: ## Run CI checks locally
	@echo "$(BLUE)Running CI checks locally...$(NC)"
	make lint
	make type-check
	make security-scan
	make test
	@echo "$(GREEN)CI checks complete!$(NC)"

# =============================================================================
# Setup
# =============================================================================

setup: ## Initial setup for new developers
	@echo "$(BLUE)Setting up Code Factory Platform...$(NC)"
	cp .env.example .env
	@echo "$(YELLOW)Please update .env file with your configuration$(NC)"
	make install-dev
	@echo "$(GREEN)Setup complete! Edit .env and run 'make docker-up' to start$(NC)"

setup-monitoring: ## Setup monitoring stack (Prometheus, Grafana)
	@echo "$(BLUE)Setting up monitoring...$(NC)"
	mkdir -p monitoring/prometheus monitoring/grafana/dashboards monitoring/grafana/datasources
	@echo "$(GREEN)Monitoring setup complete!$(NC)"

# =============================================================================
# Git Hooks
# =============================================================================

install-hooks: ## Install git hooks for pre-commit checks
	@echo "$(BLUE)Installing git hooks...$(NC)"
	@if [ -f .git/hooks/pre-commit ]; then \
		echo "$(YELLOW)Pre-commit hook already exists, appending commands...$(NC)"; \
		if ! grep -q "make lint" .git/hooks/pre-commit; then \
			echo "" >> .git/hooks/pre-commit; \
			echo "# Added by Code Factory Makefile" >> .git/hooks/pre-commit; \
			echo "make lint" >> .git/hooks/pre-commit; \
		fi; \
		if ! grep -q "make test" .git/hooks/pre-commit; then \
			echo "make test" >> .git/hooks/pre-commit; \
		fi; \
		chmod +x .git/hooks/pre-commit; \
	else \
		echo '#!/bin/sh' > .git/hooks/pre-commit; \
		echo 'make lint' >> .git/hooks/pre-commit; \
		echo 'make test' >> .git/hooks/pre-commit; \
		chmod +x .git/hooks/pre-commit; \
	fi
	@echo "$(GREEN)Git hooks installed!$(NC)"

# =============================================================================
# Version Management
# =============================================================================

version: ## Show current version
	@echo "$(BLUE)Code Factory Platform v1.0.0$(NC)"

bump-version: ## Bump version (requires bump2version)
	@echo "$(BLUE)Bumping version...$(NC)"
	pip install bump2version
	bump2version patch
	@echo "$(GREEN)Version bumped!$(NC)"

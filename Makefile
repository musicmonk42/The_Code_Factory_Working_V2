# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Code Factory Platform Makefile
# This file provides convenient commands for development, testing, and deployment

.PHONY: help install install-dev test lint format clean docker-build docker-up docker-down deploy-staging deploy-production \
	k8s-deploy-dev k8s-deploy-staging k8s-deploy-prod k8s-status k8s-logs k8s-validate \
	helm-install helm-uninstall helm-template helm-lint helm-package helm-status

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
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest -v --tb=short
	@echo "$(GREEN)Tests complete!$(NC)"

test-collect: ## Verify pytest can collect all tests without errors
	@echo "$(BLUE)Verifying pytest collection...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest --collect-only -q
	@echo "$(GREEN)Collection verification complete!$(NC)"

test-generator: ## Run Generator tests
	@echo "$(BLUE)Running Generator tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && cd generator && pytest tests/ -v --tb=short

test-omnicore: ## Run OmniCore Engine tests
	@echo "$(BLUE)Running OmniCore Engine tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && cd omnicore_engine && pytest tests/ -v --tb=short

test-sfe: ## Run Self-Fixing Engineer tests
	@echo "$(BLUE)Running Self-Fixing Engineer tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && cd self_fixing_engineer && pytest tests/ -v --tb=short

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest --cov --cov-report=html --cov-report=term -v
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
	black --check --extend-exclude "(test_project.*|bad_syntax\.py|many_bad_files)" generator/ omnicore_engine/ self_fixing_engineer/ *.py
	@echo "$(YELLOW)Running Ruff...$(NC)"
	ruff check generator/ omnicore_engine/ self_fixing_engineer/ *.py
	@echo "$(YELLOW)Running Flake8...$(NC)"
	flake8 generator/ omnicore_engine/ self_fixing_engineer/ *.py --count --select=E9,F63,F7,F82 --show-source --statistics
	@echo "$(GREEN)Linting complete!$(NC)"

format: ## Format code with Black
	@echo "$(BLUE)Formatting code...$(NC)"
	black --extend-exclude "(test_project.*|bad_syntax\.py|many_bad_files)" generator/ omnicore_engine/ self_fixing_engineer/
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
# Audit Configuration
# =============================================================================

audit-config-validate: ## Validate audit log configuration
	@echo "$(BLUE)Validating audit log configuration...$(NC)"
	python generator/audit_log/validate_config.py --config generator/audit_config.yaml
	@echo "$(GREEN)Audit configuration validation complete!$(NC)"

audit-config-validate-prod: ## Validate production audit configuration
	@echo "$(BLUE)Validating production audit log configuration...$(NC)"
	python generator/audit_log/validate_config.py --config generator/audit_config.production.yaml
	@echo "$(GREEN)Production audit configuration validation complete!$(NC)"

audit-config-validate-dev: ## Validate development audit configuration
	@echo "$(BLUE)Validating development audit log configuration...$(NC)"
	python generator/audit_log/validate_config.py --config generator/audit_config.development.yaml
	@echo "$(GREEN)Development audit configuration validation complete!$(NC)"

audit-config-validate-env: ## Validate audit configuration from environment variables
	@echo "$(BLUE)Validating audit log configuration from environment...$(NC)"
	python generator/audit_log/validate_config.py --env
	@echo "$(GREEN)Environment audit configuration validation complete!$(NC)"

audit-config-validate-strict: ## Validate audit configuration in strict mode (warnings = errors)
	@echo "$(BLUE)Validating audit log configuration (strict mode)...$(NC)"
	python generator/audit_log/validate_config.py --config generator/audit_config.yaml --strict
	@echo "$(GREEN)Strict audit configuration validation complete!$(NC)"

audit-config-setup-prod: ## Set up production audit configuration
	@echo "$(BLUE)Setting up production audit configuration...$(NC)"
	@if [ -f generator/audit_config.yaml ]; then \
		echo "$(YELLOW)Backing up existing audit_config.yaml...$(NC)"; \
		cp generator/audit_config.yaml generator/audit_config.yaml.backup; \
	fi
	cp generator/audit_config.production.yaml generator/audit_config.yaml
	@echo "$(GREEN)Production audit configuration copied to generator/audit_config.yaml$(NC)"
	@echo "$(YELLOW)Review and update with your environment-specific values$(NC)"
	@echo "$(YELLOW)Run 'make audit-config-validate' to verify$(NC)"

audit-config-setup-dev: ## Set up development audit configuration
	@echo "$(BLUE)Setting up development audit configuration...$(NC)"
	@if [ -f generator/audit_config.yaml ]; then \
		echo "$(YELLOW)Backing up existing audit_config.yaml...$(NC)"; \
		cp generator/audit_config.yaml generator/audit_config.yaml.backup; \
	fi
	cp generator/audit_config.development.yaml generator/audit_config.yaml
	@echo "$(GREEN)Development audit configuration copied to generator/audit_config.yaml$(NC)"
	@echo "$(YELLOW)Run 'make audit-config-validate' to verify$(NC)"

# =============================================================================
# Server and API
# =============================================================================

audit-config-api-docs: ## Show audit configuration API endpoints
	@echo "$(BLUE)Audit Configuration API Endpoints$(NC)"
	@echo ""
	@echo "$(YELLOW)Configuration Status:$(NC)"
	@echo "  GET /audit/config/status"
	@echo "  Returns current audit configuration, security status, and validation results"
	@echo ""
	@echo "$(YELLOW)Configuration Documentation:$(NC)"
	@echo "  GET /audit/config/documentation"
	@echo "  Returns comprehensive configuration reference and quick start guides"
	@echo ""
	@echo "$(YELLOW)Usage:$(NC)"
	@echo "  1. Start server: make run-server"
	@echo "  2. Open API docs: http://localhost:8000/docs"
	@echo "  3. Navigate to 'Audit Logs' section"
	@echo "  4. Test new endpoints"
	@echo ""
	@echo "$(YELLOW)Or use curl:$(NC)"
	@echo "  curl http://localhost:8000/audit/config/status"
	@echo "  curl http://localhost:8000/audit/config/documentation"
	@echo ""

run-server: ## Run the unified Code Factory server
	@echo "$(BLUE)Starting Code Factory server...$(NC)"
	@echo "$(YELLOW)API docs: http://localhost:8000/docs$(NC)"
	@echo "$(YELLOW)Audit config: http://localhost:8000/audit/config/status$(NC)"
	cd server && python main.py

# =============================================================================
# Docker
# =============================================================================

docker-build: ## Build unified platform Docker image
	@echo "$(BLUE)Building unified Code Factory platform image...$(NC)"
	docker build -t code-factory:latest -f Dockerfile .
	@echo "$(GREEN)Docker image built successfully!$(NC)"
	@echo "$(YELLOW)Note: The unified image includes Generator, OmniCore, and SFE modules$(NC)"

docker-up: ## Start all services with Docker Compose
	@echo "$(BLUE)Starting Docker Compose services...$(NC)"
	docker compose up -d
	@echo "$(GREEN)Services started!$(NC)"
	@echo "$(YELLOW)Generator: http://localhost:8000$(NC)"
	@echo "$(YELLOW)OmniCore: http://localhost:8001$(NC)"
	@echo "$(YELLOW)OmniCore Prometheus Metrics: http://localhost:9090/metrics$(NC)"
	@echo "$(YELLOW)Grafana: http://localhost:3000$(NC)"
	@echo "$(YELLOW)Prometheus Server: http://localhost:9090$(NC)"

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

docker-validate: ## Validate Docker build and configuration
	@echo "$(BLUE)Running Docker validation...$(NC)"
	./validate_docker_build.sh
	@echo "$(GREEN)Docker validation complete!$(NC)"

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
# Kubernetes
# =============================================================================

k8s-deploy-dev: ## Deploy to Kubernetes (development)
	@echo "$(BLUE)Deploying to Kubernetes development environment...$(NC)"
	kubectl apply -k k8s/overlays/development
	@echo "$(GREEN)Deployed to development!$(NC)"

k8s-deploy-staging: ## Deploy to Kubernetes (staging)
	@echo "$(BLUE)Deploying to Kubernetes staging environment...$(NC)"
	kubectl apply -k k8s/overlays/staging
	@echo "$(GREEN)Deployed to staging!$(NC)"

k8s-deploy-prod: ## Deploy to Kubernetes (production)
	@echo "$(RED)Deploying to Kubernetes production environment...$(NC)"
	kubectl apply -k k8s/overlays/production
	@echo "$(GREEN)Deployed to production!$(NC)"

k8s-status: ## Show Kubernetes deployment status
	@echo "$(BLUE)Kubernetes Deployment Status:$(NC)"
	@kubectl get all -n codefactory 2>/dev/null || echo "No resources in codefactory namespace"

k8s-status-dev: ## Show Kubernetes deployment status (development)
	@echo "$(BLUE)Development Environment Status:$(NC)"
	kubectl get all -n codefactory-dev

k8s-status-staging: ## Show Kubernetes deployment status (staging)
	@echo "$(BLUE)Staging Environment Status:$(NC)"
	kubectl get all -n codefactory-staging

k8s-status-prod: ## Show Kubernetes deployment status (production)
	@echo "$(BLUE)Production Environment Status:$(NC)"
	kubectl get all -n codefactory-production

k8s-logs: ## Show Kubernetes pod logs
	@echo "$(BLUE)Showing API pod logs...$(NC)"
	kubectl logs -f -l app=codefactory-api -n codefactory --tail=100

k8s-logs-dev: ## Show Kubernetes pod logs (development)
	kubectl logs -f -l app=codefactory-api -n codefactory-dev --tail=100

k8s-logs-staging: ## Show Kubernetes pod logs (staging)
	kubectl logs -f -l app=codefactory-api -n codefactory-staging --tail=100

k8s-logs-prod: ## Show Kubernetes pod logs (production)
	kubectl logs -f -l app=codefactory-api -n codefactory-production --tail=100

k8s-delete-dev: ## Delete Kubernetes resources (development)
	@echo "$(RED)Deleting development environment...$(NC)"
	kubectl delete -k k8s/overlays/development
	@echo "$(GREEN)Development environment deleted!$(NC)"

k8s-delete-staging: ## Delete Kubernetes resources (staging)
	@echo "$(RED)Deleting staging environment...$(NC)"
	kubectl delete -k k8s/overlays/staging
	@echo "$(GREEN)Staging environment deleted!$(NC)"

k8s-delete-prod: ## Delete Kubernetes resources (production)
	@echo "$(RED)WARNING: Deleting production environment...$(NC)"
	@read -p "Are you sure? Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted" && exit 1)
	kubectl delete -k k8s/overlays/production
	@echo "$(GREEN)Production environment deleted!$(NC)"

k8s-validate: ## Validate Kubernetes manifests
	@echo "$(BLUE)Validating Kubernetes manifests...$(NC)"
	kubectl apply --dry-run=client -k k8s/base
	@echo "$(GREEN)Validation complete!$(NC)"

# =============================================================================
# Helm
# =============================================================================

helm-install: ## Install with Helm (development)
	@echo "$(BLUE)Installing Code Factory with Helm...$(NC)"
	helm upgrade --install codefactory ./helm/codefactory \
		--create-namespace \
		--namespace codefactory \
		--set image.tag=latest
	@echo "$(GREEN)Helm release installed!$(NC)"

helm-install-dev: ## Install with Helm (development environment)
	@echo "$(BLUE)Installing Code Factory with Helm (dev)...$(NC)"
	helm upgrade --install codefactory-dev ./helm/codefactory \
		--create-namespace \
		--namespace codefactory-dev \
		--set image.tag=dev \
		--set replicaCount=1 \
		--set resources.limits.cpu=1000m \
		--set resources.limits.memory=2Gi
	@echo "$(GREEN)Dev Helm release installed!$(NC)"

helm-install-prod: ## Install with Helm (production environment)
	@echo "$(BLUE)Installing Code Factory with Helm (production)...$(NC)"
	helm upgrade --install codefactory-prod ./helm/codefactory \
		--create-namespace \
		--namespace codefactory-production \
		--set image.tag=latest \
		--set replicaCount=3 \
		--set autoscaling.enabled=true
	@echo "$(GREEN)Production Helm release installed!$(NC)"

helm-uninstall: ## Uninstall Helm release
	@echo "$(RED)Uninstalling Helm release...$(NC)"
	helm uninstall codefactory -n codefactory
	@echo "$(GREEN)Helm release uninstalled!$(NC)"

helm-uninstall-dev: ## Uninstall Helm release (development)
	@echo "$(RED)Uninstalling dev Helm release...$(NC)"
	helm uninstall codefactory-dev -n codefactory-dev
	@echo "$(GREEN)Dev Helm release uninstalled!$(NC)"

helm-uninstall-prod: ## Uninstall Helm release (production)
	@echo "$(RED)WARNING: Uninstalling production Helm release...$(NC)"
	@read -p "Are you sure? Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted" && exit 1)
	helm uninstall codefactory-prod -n codefactory-production
	@echo "$(GREEN)Production Helm release uninstalled!$(NC)"

helm-template: ## Show Helm template output
	@echo "$(BLUE)Rendering Helm templates...$(NC)"
	helm template codefactory ./helm/codefactory

helm-lint: ## Lint Helm chart
	@echo "$(BLUE)Linting Helm chart...$(NC)"
	helm lint helm/codefactory
	@echo "$(GREEN)Helm chart lint complete!$(NC)"

helm-package: ## Package Helm chart
	@echo "$(BLUE)Packaging Helm chart...$(NC)"
	helm package helm/codefactory -d dist/
	@echo "$(GREEN)Helm chart packaged!$(NC)"

helm-status: ## Show Helm release status
	@echo "$(BLUE)Helm Release Status:$(NC)"
	helm list -A | grep codefactory || echo "No codefactory releases found"

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

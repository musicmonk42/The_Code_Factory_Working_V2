# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Code Factory Platform Makefile
# This file provides convenient commands for development, testing, and deployment

# Use bash with pipefail so any command in a pipeline that fails causes the recipe to fail
SHELL := /bin/bash -o pipefail

.PHONY: help install install-dev install-ai install-mutation-tools test lint format clean docker-build docker-build-ai docker-up docker-down deploy-staging deploy-production \
	k8s-deploy-dev k8s-deploy-staging k8s-deploy-prod k8s-status k8s-logs k8s-validate \
	helm-install helm-uninstall helm-template helm-lint helm-package helm-status \
	db-migrate db-migrate-create db-migrate-history db-migrate-current db-migrate-downgrade db-migrate-validate \
	docs docs-serve docs-clean \
	validate-few-shot mutation-test codegen-multipass-status \
	test-arbiter-policy test-arbiter-integration test-codegen-stubs test-pipeline-fixes \
	test-evolution test-rl-integration \
	chaincode-build chaincode-test chaincode-vet chaincode-lint chaincode-coverage chaincode-clean

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
	pip install pytest pytest-cov pytest-asyncio pytest-mock black ruff flake8 mypy bandit safety pip-audit "pylint>=3.0.0,<4.0.0"
	@echo "$(GREEN)Development installation complete!$(NC)"

install-ai: ## Install optional Tier-1 AI capability dependencies (qiskit, nengo, opencv)
	@echo "$(BLUE)Installing optional AI dependencies (Tier-1 capabilities)...$(NC)"
	@echo "$(YELLOW)Note: These are optional extras. Missing deps degrade gracefully to NumPy/rule-based fallbacks.$(NC)"
	pip install --upgrade pip setuptools wheel
	pip install -r requirements-ai.txt
	@echo "$(GREEN)Optional AI dependencies installed!$(NC)"
	@echo "$(YELLOW)Set INSTALL_AI_DEPS=1 when building Docker images to include these deps.$(NC)"

install-mutation-tools: ## Install mutation testing tools used by MutationTester (mutmut or cosmic-ray)
	@echo "$(BLUE)Installing mutation testing tools...$(NC)"
	@echo "$(YELLOW)MutationTester will automatically use mutmut if installed, or cosmic-ray as a fallback.$(NC)"
	pip install --upgrade pip
	pip install mutmut
	@echo "$(GREEN)mutmut installed. Run 'make mutation-test' to execute mutation tests.$(NC)"
	@echo "$(YELLOW)Optional: install cosmic-ray as an alternative: pip install cosmic-ray$(NC)"

# =============================================================================
# Testing
# =============================================================================

test: ## Run all tests
	@echo "$(BLUE)Running all tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest -v --tb=short
	@echo "$(GREEN)Tests complete!$(NC)"

test-large-spec-fixes: ## Run tests for large-spec pipeline fixes (multi-pass, ensemble, additive retry)
	@echo "$(BLUE)Running large-spec pipeline fix tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest tests/test_large_spec_pipeline_fixes.py -v --tb=short
	@echo "$(GREEN)Large-spec fix tests complete!$(NC)"

test-pipeline-fixes: ## Run tests for 5 pipeline fixes (agent registration, README venv, critique report, plugin fallback, requirements.txt)
	@echo "$(BLUE)Running pipeline fix validation tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest tests/test_production_log_pipeline_fixes.py -v --tb=short
	@echo "$(GREEN)Pipeline fix tests complete!$(NC)"

test-codegen-stubs: ## Run stub-generation tests (classify, render, detection checks for new Jinja2 stub templates)
	@echo "$(BLUE)Running codegen stub-generation tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && pytest tests/test_local_module_stubs.py -v --tb=short
	@echo "$(GREEN)Codegen stub-generation tests complete!$(NC)"

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

fix-imports: ## Run the self-healing import fixer CLI heal command against the project root (dry-run by default)
	@echo "$(BLUE)Running self-healing import fixer...$(NC)"
	@echo "$(YELLOW)Usage: make fix-imports ROOT=./my_project  (defaults to current directory)$(NC)"
	@echo "$(YELLOW)       make fix-imports ROOT=./my_project DRY_RUN=  (apply fixes)$(NC)"
	@python -m self_fixing_engineer.self_healing_import_fixer.cli heal $${ROOT:-.} $${DRY_RUN:---dry-run}
	@echo "$(GREEN)Import fixing complete!$(NC)"

test-plugin-agents: ## Run plugin agent integration tests (all 8 agents: refactor, healer, judge, ethics, simulation, ci_cd, human, oracle) + _agent_base unit tests
	@echo "$(BLUE)Running plugin agent integration tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && \
		pytest tests/test_refactor_agent_integration.py -v --tb=short
	@echo "$(GREEN)Plugin agent integration tests complete!$(NC)"

test-arbiter-policy: ## Run Arbiter policy engine unit tests (PolicyEngine, PolicyManager, facade)
	@echo "$(BLUE)Running Arbiter policy unit tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && \
		pytest self_fixing_engineer/tests/test_arbiter_policy_core.py \
		       self_fixing_engineer/tests/test_arbiter_policy_policy_e2e.py \
		       -v --tb=short
	@echo "$(GREEN)Arbiter policy unit tests complete!$(NC)"

test-arbiter-integration: ## Run full Arbiter integration tests (PolicyEngine→Facade, Constitution, SFEService)
	@echo "$(BLUE)Running Arbiter integration tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" \
		ARBITER_WORLD_SIZE=2 ARBITER_ROLE=admin \
		POLICY_CONFIG_FILE_PATH="/tmp/test_policies.json" && \
		pytest self_fixing_engineer/tests/ tests/test_stubs.py \
		       -k "arbiter or policy or facade or constitution" \
		       -v --tb=short
	@echo "$(GREEN)Arbiter integration tests complete!$(NC)"

test-evolution: ## Run Genetic Algorithm / Evolution Engine tests (EV-2, EV-3, IB-2 fixes)
	@echo "$(BLUE)Running Evolution Engine tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" \
		EVOLUTION_POPULATION_SIZE=4 EVOLUTION_GENERATIONS=2 \
		EVOLUTION_BACKEND=auto \
		EXPLORER_EVOLUTION_GENERATIONS=2 EXPLORER_EVOLUTION_POPULATION_SIZE=3 \
		MIN_SUPERVISED_TRAINING_SAMPLES=5 \
		ENABLE_EXPERIMENTAL_EVOLUTION=true && \
		pytest -k "evolution or evolve or genetic or deap or gene" -v --tb=short
	@echo "$(GREEN)Evolution Engine tests complete!$(NC)"

test-rl-integration: ## Run RL stack end-to-end integration tests (arbiter metrics, PPO, GA persistence, arena, meta-learning)
	@echo "$(BLUE)Running RL integration tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" \
		EVOLUTION_BACKEND=auto \
		EXPLORER_EVOLUTION_GENERATIONS=2 EXPLORER_EVOLUTION_POPULATION_SIZE=3 \
		MIN_SUPERVISED_TRAINING_SAMPLES=5 \
		ENABLE_EXPERIMENTAL_EVOLUTION=true && \
		pytest tests/test_arbiter_rl_integration.py -v --tb=short
	@echo "$(GREEN)RL integration tests complete!$(NC)"

test-dlt: ## Run DLT backend tests (RB-5 EVM support, IB-1 checkpoint bridge, SEC-1 HMAC)
	@echo "$(BLUE)Running DLT backend tests...$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" \
		DLT_TYPE=fabric PRODUCTION_MODE=0 \
		DLT_HMAC_KEY="dev-dlt-hmac-key-not-for-production" \
		DLT_ENCRYPTION_KEY="" DLT_ENCRYPT_AT_REST=false \
		S3_BUCKET_NAME="codefactory-dlt-offchain-dev" S3_REGION="us-east-1" \
		FABRIC_CHANNEL="codefactory-channel" FABRIC_CHAINCODE="codefactory-cc" \
		FABRIC_ORG="Org1" FABRIC_USER="Admin" \
		FABRIC_NETWORK_PROFILE="/tmp/fabric-connection-profile.json" && \
		pytest -k "dlt or checkpoint_manager or hmac or dlt_backend" -v --tb=short
	@echo "$(GREEN)DLT backend tests complete!$(NC)"

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
	black --check --extend-exclude "(test_project.*|bad_syntax\.py|many_bad_files)" generator/ omnicore_engine/ self_fixing_engineer/ shared/ *.py
	@echo "$(YELLOW)Running Ruff...$(NC)"
	ruff check generator/ omnicore_engine/ self_fixing_engineer/ shared/ *.py
	@echo "$(YELLOW)Running Flake8...$(NC)"
	flake8 generator/ omnicore_engine/ self_fixing_engineer/ shared/ *.py --count --select=E9,F63,F7,F82 --show-source --statistics
	@echo "$(YELLOW)Running Pylint...$(NC)"
	pylint generator/ omnicore_engine/ self_fixing_engineer/ shared/ --errors-only --disable=all --enable=E
	@echo "$(GREEN)Linting complete!$(NC)"

format: ## Format code with Black
	@echo "$(BLUE)Formatting code...$(NC)"
	black --extend-exclude "(test_project.*|bad_syntax\.py|many_bad_files)" generator/ omnicore_engine/ self_fixing_engineer/ shared/
	@echo "$(GREEN)Code formatted!$(NC)"

type-check: ## Run type checking with mypy
	@echo "$(BLUE)Running type checks...$(NC)"
	mypy generator/ omnicore_engine/ self_fixing_engineer/ shared/
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

# =============================================================================
# Secret Validation
# =============================================================================

validate-secrets: ## Validate all required secrets are configured
	@echo "$(BLUE)Validating secrets configuration...$(NC)"
	python scripts/validate_secrets.py
	@echo "$(GREEN)Secret validation complete!$(NC)"

validate-secrets-strict: ## Validate secrets in strict mode (warnings = errors)
	@echo "$(BLUE)Validating secrets configuration (strict mode)...$(NC)"
	python scripts/validate_secrets.py --strict
	@echo "$(GREEN)Strict secret validation complete!$(NC)"

validate-secrets-json: ## Validate secrets and output JSON (for CI/CD)
	@echo "$(BLUE)Validating secrets configuration (JSON output)...$(NC)"
	python scripts/validate_secrets.py --json

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
	python server/run.py

# =============================================================================
# Docker
# =============================================================================

docker-build: ## Build unified platform Docker image
	@echo "$(BLUE)Building unified Code Factory platform image...$(NC)"
	docker build \
		--build-arg BUILD_DATE="$$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		-t code-factory:latest \
		-f Dockerfile .
	@echo "$(GREEN)Docker image built successfully!$(NC)"
	@echo "$(YELLOW)Note: The unified image includes Generator, OmniCore, and SFE modules$(NC)"

docker-build-ai: ## Build Docker image with optional Tier-1 AI capabilities (qiskit, nengo, opencv)
	@echo "$(BLUE)Building Code Factory AI-full image (includes quantum + neuromorphic backends)...$(NC)"
	docker build \
		--build-arg BUILD_DATE="$$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		--build-arg INSTALL_AI_DEPS=1 \
		-t code-factory:ai-full \
		-f Dockerfile .
	@echo "$(GREEN)Docker AI-full image built successfully!$(NC)"
	@echo "$(YELLOW)AI capabilities: quantum array backend, neuromorphic backend, OpenCV video analysis$(NC)"

docker-up: ## Start all services with Docker Compose
	@echo "$(BLUE)Starting Docker Compose services...$(NC)"
	docker compose up -d
	@echo "$(GREEN)Services started!$(NC)"
	@echo "$(YELLOW)Code Factory API:    http://localhost:8000$(NC)"
	@echo "$(YELLOW)API Docs:            http://localhost:8000/docs$(NC)"
	@echo "$(YELLOW)Prometheus Metrics:  http://localhost:9090/metrics$(NC)"
	@echo "$(YELLOW)Prometheus Server:   http://localhost:9091$(NC)"
	@echo "$(YELLOW)Grafana:             http://localhost:3000$(NC)"

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

deployment-validate: ## Validate generated deployment files (Docker, K8s, Helm)
	@echo "$(BLUE)Validating generated deployment files...$(NC)"
	@echo "$(YELLOW)This validates deployment artifacts from code generation jobs$(NC)"
	@if [ -d "./uploads" ]; then \
		python3 -c "import asyncio; from generator.agents.deploy_agent.deploy_validator import DeploymentCompletenessValidator; \
		import sys; \
		validator = DeploymentCompletenessValidator(); \
		result = asyncio.run(validator.validate('', 'all')); \
		print(f\"Status: {result.get('status')}\"); \
		errors = result.get('errors', []); \
		[print(f\"ERROR: {e}\") for e in errors]; \
		sys.exit(0 if result.get('status') == 'passed' else 1)"; \
	else \
		echo "$(YELLOW)No uploads directory found. Run code generation first.$(NC)"; \
	fi
	@echo "$(GREEN)Deployment validation complete!$(NC)"

mutation-test: ## Run mutation tests with mutmut (targets generator/main/provenance.py, generator/utils/project_endpoint_analyzer.py, and testgen_agent.py)
	@echo "$(BLUE)Running mutation tests...$(NC)"
	@echo "$(YELLOW)Note: This may take several minutes. See docs/MUTATION_TESTING.md for details.$(NC)"
	@export TESTING=1 AWS_REGION="" FALLBACK_ENCRYPTION_KEY="dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ=" && mutmut run --no-progress
	mutmut results
	@echo "$(GREEN)Mutation test run complete! Check results above.$(NC)"

validate-few-shot: ## Validate project-level few-shot examples for deploy agent
	@echo "$(BLUE)Validating project-level few-shot examples...$(NC)"
	@python3 -c "\
import glob, json, sys; \
errors = []; \
files = glob.glob('deploy_templates/few_shot_examples/*.json'); \
[errors.append(f'MISSING deploy_templates/few_shot_examples/') or sys.exit(1)] if not files else None; \
[errors.extend([ \
    (f'{f}: missing key' + k) \
    for f in files \
    for k in ('query', 'example') \
    if k not in (d := json.load(open(f))) \
]) for f in files for d in [json.load(open(f))]]; \
[print(f'ERROR: {e}') for e in errors]; \
print(f'✓ {len(files)} few-shot examples validated') if not errors else sys.exit(1)"
	@export TESTING=1 && pytest generator/tests/test_agents_deploy_prompt.py::TestProjectFewShotExamples -v --tb=short
	@echo "$(GREEN)Few-shot validation complete!$(NC)"

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

db-migrate: ## Run database migrations with Alembic
	@echo "$(BLUE)Running database migrations with Alembic...$(NC)"
	alembic upgrade head
	@echo "$(GREEN)Migrations complete!$(NC)"

db-migrate-create: ## Create a new database migration
	@echo "$(BLUE)Creating new migration...$(NC)"
	@read -p "Enter migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"
	@echo "$(GREEN)Migration created!$(NC)"

db-migrate-history: ## Show migration history
	@echo "$(BLUE)Migration history:$(NC)"
	alembic history

db-migrate-current: ## Show current migration version
	@echo "$(BLUE)Current migration version:$(NC)"
	alembic current

db-migrate-downgrade: ## Downgrade database by one migration
	@echo "$(YELLOW)Downgrading database by one migration...$(NC)"
	alembic downgrade -1
	@echo "$(GREEN)Downgrade complete!$(NC)"

db-migrate-validate: ## Validate Alembic configuration
	@echo "$(BLUE)Validating Alembic configuration...$(NC)"
	@python3 -c "from alembic.config import Config; from alembic.script import ScriptDirectory; cfg = Config('alembic.ini'); ScriptDirectory.from_config(cfg); print('✓ Alembic configuration is valid')"
	@echo "$(GREEN)Validation complete!$(NC)"

db-reset: ## Reset database (WARNING: destroys data)
	@echo "$(RED)Resetting database...$(NC)"
	rm -f dev.db deploy_agent_history.db mock_history.db omnicore.db test_omnicore.db
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

k8s-deploy-prod: ## Deploy to Kubernetes (production) — usage: make k8s-deploy-prod IMAGE_TAG=<git-sha-or-semver>
	@# IMAGE_TAG must be passed as a Make variable: make k8s-deploy-prod IMAGE_TAG=abc1234
	@# It intentionally cannot be set as a shell environment variable to force explicit intent.
	@if [ -z "$(IMAGE_TAG)" ]; then \
		echo "$(RED)ERROR: IMAGE_TAG must be set for production deployments. Never use 'latest'.$(NC)"; \
		echo "$(YELLOW)Usage: make k8s-deploy-prod IMAGE_TAG=<git-sha-or-semver-tag>$(NC)"; \
		exit 1; \
	fi
	@echo "$(RED)Deploying to Kubernetes production environment with image tag: $(IMAGE_TAG)$(NC)"
	@read -p "Are you sure you want to deploy to PRODUCTION? Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted" && exit 1)
	kubectl kustomize k8s/overlays/production \
		| sed "s|ghcr.io/musicmonk42/codefactory:[a-zA-Z0-9._-]*|ghcr.io/musicmonk42/codefactory:$(IMAGE_TAG)|g" \
		| kubectl apply -f -
	@echo "$(GREEN)Deployed to production with image tag: $(IMAGE_TAG)$(NC)"

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
		--set image.tag=latest \
		--wait --atomic
	@echo "$(GREEN)Helm release installed!$(NC)"

helm-install-dev: ## Install with Helm (development environment)
	@echo "$(BLUE)Installing Code Factory with Helm (dev)...$(NC)"
	helm upgrade --install codefactory-dev ./helm/codefactory \
		--create-namespace \
		--namespace codefactory-dev \
		--set image.tag=dev \
		--set replicaCount=1 \
		--set resources.limits.cpu=1000m \
		--set resources.limits.memory=2Gi \
		--wait --atomic
	@echo "$(GREEN)Dev Helm release installed!$(NC)"

helm-install-prod: ## Install with Helm (production environment) — usage: make helm-install-prod IMAGE_TAG=<git-sha-or-semver>
	@echo "$(BLUE)Installing Code Factory with Helm (production)...$(NC)"
	@# IMAGE_TAG must be passed as a Make variable: make helm-install-prod IMAGE_TAG=abc1234
	@# It intentionally cannot be set as a shell environment variable to force explicit intent.
	@if [ -z "$(IMAGE_TAG)" ]; then \
		echo "$(RED)ERROR: IMAGE_TAG must be set for production deployments. Never use 'latest'.$(NC)"; \
		echo "$(YELLOW)Usage: make helm-install-prod IMAGE_TAG=<git-sha-or-semver-tag>$(NC)"; \
		exit 1; \
	fi
	helm upgrade --install codefactory-prod ./helm/codefactory \
		--create-namespace \
		--namespace codefactory-production \
		--set image.tag=$(IMAGE_TAG) \
		--set replicaCount=3 \
		--set autoscaling.enabled=true \
		--wait --atomic --timeout 10m
	@echo "$(GREEN)Production Helm release installed with image tag: $(IMAGE_TAG)$(NC)"

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
# Fabric Chaincode
# =============================================================================

CHAINCODE_DIR := self_fixing_engineer/fabric_chaincode

chaincode-build: ## Build the Fabric checkpoint chaincode (verifies compilation)
	@echo "$(BLUE)Building Fabric checkpoint chaincode...$(NC)"
	@command -v go >/dev/null 2>&1 || { echo "$(RED)ERROR: 'go' not found — install Go 1.23+$(NC)"; exit 1; }
	cd $(CHAINCODE_DIR) && go build ./...
	@echo "$(GREEN)Chaincode build successful.$(NC)"

chaincode-test: ## Run unit tests for the Fabric checkpoint chaincode
	@echo "$(BLUE)Running chaincode unit tests...$(NC)"
	@command -v go >/dev/null 2>&1 || { echo "$(RED)ERROR: 'go' not found — install Go 1.23+$(NC)"; exit 1; }
	cd $(CHAINCODE_DIR) && go test -v -count=1 -race ./...
	@echo "$(GREEN)Chaincode unit tests passed.$(NC)"

chaincode-coverage: ## Run chaincode tests with HTML coverage report
	@echo "$(BLUE)Running chaincode tests with coverage...$(NC)"
	@command -v go >/dev/null 2>&1 || { echo "$(RED)ERROR: 'go' not found — install Go 1.23+$(NC)"; exit 1; }
	cd $(CHAINCODE_DIR) && \
		go test -v -count=1 -coverprofile=coverage.out ./... && \
		go tool cover -html=coverage.out -o coverage.html
	@echo "$(GREEN)Coverage report: $(CHAINCODE_DIR)/coverage.html$(NC)"

chaincode-vet: ## Run go vet on the chaincode
	@echo "$(BLUE)Running go vet on chaincode...$(NC)"
	@command -v go >/dev/null 2>&1 || { echo "$(RED)ERROR: 'go' not found — install Go 1.23+$(NC)"; exit 1; }
	cd $(CHAINCODE_DIR) && go vet ./...
	@echo "$(GREEN)go vet passed with no issues.$(NC)"

chaincode-lint: ## Run staticcheck + govulncheck on the chaincode
	@echo "$(BLUE)Linting chaincode (staticcheck + govulncheck)...$(NC)"
	@command -v go >/dev/null 2>&1 || { echo "$(RED)ERROR: 'go' not found — install Go 1.23+$(NC)"; exit 1; }
	@command -v staticcheck >/dev/null 2>&1 || { \
		echo "$(YELLOW)staticcheck not found — installing...$(NC)"; \
		go install honnef.co/go/tools/cmd/staticcheck@latest; \
	}
	@command -v govulncheck >/dev/null 2>&1 || { \
		echo "$(YELLOW)govulncheck not found — installing...$(NC)"; \
		go install golang.org/x/vuln/cmd/govulncheck@latest; \
	}
	cd $(CHAINCODE_DIR) && staticcheck ./...
	cd $(CHAINCODE_DIR) && govulncheck ./...
	@echo "$(GREEN)Chaincode lint and vulnerability scan passed.$(NC)"

chaincode-clean: ## Remove chaincode build artifacts and coverage files
	@echo "$(BLUE)Cleaning chaincode artifacts...$(NC)"
	rm -f $(CHAINCODE_DIR)/coverage.out $(CHAINCODE_DIR)/coverage.html
	rm -f $(CHAINCODE_DIR)/checkpoint_chaincode
	@echo "$(GREEN)Chaincode artifacts removed.$(NC)"

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
	$(MAKE) chaincode-clean 2>/dev/null || true
	@echo "$(GREEN)Cleanup complete!$(NC)"

clean-all: clean docker-clean db-reset docs-clean ## Deep clean (removes Docker resources, databases, and doc build output)
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

docs: ## Build Sphinx HTML documentation (output: docs/_build/html/)
	@echo "$(BLUE)Building Sphinx HTML documentation...$(NC)"
	@command -v sphinx-build >/dev/null 2>&1 || { \
		echo "$(YELLOW)sphinx-build not found — installing Sphinx toolchain...$(NC)"; \
		pip install "sphinx>=7.0.0" "sphinx-rtd-theme>=2.0.0" "myst-parser>=3.0.0"; \
	}
	sphinx-build -b html --keep-going docs docs/_build/html
	@echo "$(GREEN)Documentation built: docs/_build/html/index.html$(NC)"

docs-serve: ## Serve Sphinx documentation locally on http://localhost:8080
	@echo "$(BLUE)Serving documentation at http://localhost:8080...$(NC)"
	@if [ ! -f docs/_build/html/index.html ]; then \
		echo "$(YELLOW)Documentation not built yet — running 'make docs' first...$(NC)"; \
		$(MAKE) docs; \
	fi
	@command -v python3 >/dev/null 2>&1 && \
		python3 -m http.server 8080 --directory docs/_build/html || \
		python -m http.server 8080 --directory docs/_build/html

docs-clean: ## Remove Sphinx build output
	@echo "$(BLUE)Removing docs/_build/...$(NC)"
	rm -rf docs/_build/
	@echo "$(GREEN)Documentation build artifacts removed.$(NC)"

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
	curl -s http://localhost:9090/metrics | head -20

# =============================================================================
# CI/CD Local Testing
# =============================================================================

ci-local: ## Run CI checks locally
	@echo "$(BLUE)Running CI checks locally...$(NC)"
	$(MAKE) lint
	$(MAKE) type-check
	$(MAKE) security-scan
	$(MAKE) test
	@echo "$(GREEN)CI checks complete!$(NC)"

# =============================================================================
# Codegen Multi-Pass Diagnostics
# =============================================================================

codegen-multipass-status: ## Show current multi-pass code-generation thresholds and timeout budgets
	@echo "$(BLUE)Codegen Multi-Pass Configuration$(NC)"
	@echo ""
	@echo "$(YELLOW)Active thresholds (override via environment variables):$(NC)"
	@echo "  CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD     = $${CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD:-25}     (API endpoints; auto-enables ensemble + multi-pass)"
	@echo "  CODEGEN_MULTIPASS_FILE_THRESHOLD         = $${CODEGEN_MULTIPASS_FILE_THRESHOLD:-20}     (required files; secondary trigger)"
	@echo "  CODEGEN_MULTIPASS_MD_SIZE_THRESHOLD      = $${CODEGEN_MULTIPASS_MD_SIZE_THRESHOLD:-30000}  (spec chars; triggers multi-pass for large specs)"
	@echo "  SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD = $${SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD:-0.50}  (fraction; hard-failure gate: 0.0–1.0)"
	@echo ""
	@echo "$(YELLOW)Active timeout budgets:$(NC)"
	@echo "  PIPELINE_CODEGEN_TIMEOUT_SECONDS     = $${PIPELINE_CODEGEN_TIMEOUT_SECONDS:-900}  (outer per-job budget; 15 min default)"
	@echo "  ENSEMBLE_PROVIDER_TIMEOUT_SECONDS    = $${ENSEMBLE_PROVIDER_TIMEOUT_SECONDS:-300}  (per-provider LLM call; 5 min default)"
	@echo ""
	@echo "$(YELLOW)Behaviour:$(NC)"
	@echo "  Specs with fewer endpoints/files  → single-pass, honours config.ensemble_enabled"
	@echo "  Specs that reach either threshold → 3-pass generation (core / routes+services / infra)"
	@echo "                                       each pass uses call_ensemble_api() majority vote"
	@echo "  A periodic heartbeat task logs progress every 30 s per pass so container health"
	@echo "  checks see activity throughout long LLM calls."
	@echo ""
	@echo "$(YELLOW)Additive retry:$(NC)"
	@echo "  InsufficientOutput / SpecFidelityFailure retries KEEP existing files on disk."
	@echo "  The retry prompt lists already-generated files so the LLM adds only missing ones."
	@echo ""
	@echo "$(YELLOW)To disable multi-pass entirely:$(NC)"
	@echo "  export CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD=9999"
	@echo "  export CODEGEN_MULTIPASS_FILE_THRESHOLD=9999"
	@echo "  export CODEGEN_MULTIPASS_MD_SIZE_THRESHOLD=9999999"
	@echo ""
	@echo "$(YELLOW)To lower the threshold (enable for smaller specs):$(NC)"
	@echo "  export CODEGEN_MULTIPASS_ENDPOINT_THRESHOLD=10"
	@echo "  export CODEGEN_MULTIPASS_MD_SIZE_THRESHOLD=15000"
	@echo ""
	@echo "$(YELLOW)To relax the spec-fidelity gate (development mode):$(NC)"
	@echo "  export SPEC_FIDELITY_MISSING_ENDPOINT_THRESHOLD=0.80"
	@echo ""
	@echo "$(YELLOW)To extend the per-job timeout budget (very large specs):$(NC)"
	@echo "  export PIPELINE_CODEGEN_TIMEOUT_SECONDS=1800"
	@echo ""

# =============================================================================
# Setup
# =============================================================================

setup: ## Initial setup for new developers
	@echo "$(BLUE)Setting up Code Factory Platform...$(NC)"
	@if [ -f .env ]; then \
		echo "$(YELLOW).env already exists — skipping copy to avoid overwriting your configuration.$(NC)"; \
		echo "$(YELLOW)Delete .env and re-run 'make setup' to reset from .env.example.$(NC)"; \
	else \
		cp .env.example .env; \
		echo "$(GREEN).env created from .env.example — update it with your configuration.$(NC)"; \
	fi
	$(MAKE) install-dev
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

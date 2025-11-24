# Code Factory Platform 🚀
Code Factory v1.0.0 – The "Self-Sustaining Code" EditionProprietary Technology by Novatrax Labs
Transform natural language into production-ready applications with automated, self-healing maintenance powered by AI, DLT, and multi-agent orchestration.
Crafted with precision in Fairhope, Alabama, USA.
The Code Factory is an enterprise-grade, AI-driven ecosystem that automates the entire software development and maintenance lifecycle. It turns high-level requirements (e.g., README files) into fully functional applications, including code, tests, deployment configurations, and documentation, while continuously maintaining and enhancing them through self-healing, compliance, and optimization. Comprising the README-to-App Code Generator (RCG), OmniCore Omega Pro Engine (OmniCore), and Self-Fixing Engineer (SFE, powered by Arbiter AI), it delivers unparalleled automation, security, and scalability for developers, DevOps, and enterprises in regulated industries.
Version: 1.0.0 (August 24, 2025)License: Proprietary (© 2025 Novatrax Labs LLC)Contact: support@novatraxlabs.comIssues: <enterprise-repo-url>/issues (enterprise access required)

Table of Contents

**Quick Links**
- [QUICKSTART.md](./QUICKSTART.md) - Get started in 5 minutes
- [DEPLOYMENT.md](./DEPLOYMENT.md) - Production deployment guide
- [Makefile Commands](#makefile-commands) - Common development commands

Features
Architecture
Getting Started
Prerequisites
Installation
Quick Start (Recommended)
Manual Installation
Configuration
Environment Variables


Usage
CLI Usage
API Usage
Demo Workflow


Makefile Commands
Extending Code Factory
Custom Plugins
Custom Agents
DLT and SIEM Integrations


Key Components
Tests
Troubleshooting
Best Practices
CI/CD Pipeline
Contribution Guidelines
Roadmap
Support
License


Features

Automated Code Generation: Converts READMEs or prompts into production-ready code, tests, deployment configs (Dockerfiles, Helm), and docs using AI agents (codegen_agent, testgen_agent, deploy_agent, docgen_agent, clarifier).
Self-Healing Maintenance: SFE’s Arbiter AI (arbiter.py) fixes, updates, and optimizes code via codebase_analyzer.py, bug_manager.py, and meta_learning_orchestrator.py.
Compliance and Security: Enforces NIST/ISO standards (guardrails/compliance_mapper.py), PII redaction (security_utils.py), and tamper-evident logging (audit_log.py).
Distributed Ledger Integration: Stores checkpoints on Hyperledger Fabric (checkpoint_chaincode.go) and EVM (CheckpointContract.sol) for immutable provenance.
Observability: Prometheus metrics (metrics.py), OpenTelemetry tracing (observability_utils.py), and SIEM integration (siem_factory.py).
Multi-Agent Orchestration: Manages AI, human, and plugin agents (crew_manager.py) with RBAC and scaling (mesh/event_bus.py).
Self-Evolution: Reinforcement learning (envs/code_health_env.py) and genetic algorithms (evolution.py) optimize system health.
Multi-Modal Support: Processes PDFs, images, and text inputs (input_utils.py).
Sandboxing: Secure execution with AppArmor/seccomp (simulation/sandbox.py).


Architecture
**Note:** The Code Factory is a **unified platform** where the three primary modules (Generator, OmniCore Engine, and Self-Fixing Engineer) are tightly integrated and deployed together. They share the same dependencies, Docker image, and CI/CD pipeline as a single cohesive system.

The Code Factory is a modular, decoupled ecosystem:

README-to-App Code Generator (RCG, generator/):

Generates code, tests, configs, and docs using agents (codegen_agent.py, testgen_agent.py, etc.).
Includes bug/compliance management (critique_agent, security_utils.py).
Part of the unified platform, integrates with OmniCore.


OmniCore Omega Pro Engine (OmniCore, omnicore_engine/):

Coordinates RCG and SFE via sharded_message_bus.py.
Manages plugins (plugin_registry.py), persistence (database.py), and auditing (audit.py).
Supports CLI (cli.py) and API (fastapi_app.py).
Part of the unified platform.


Self-Fixing Engineer (SFE, self_fixing_engineer/):

Powered by Arbiter AI (arbiter.py), it handles maintenance via codebase_analyzer.py, bug_manager.py, intent_capture/agent_core.py, and mesh/checkpoint_manager.py.
Includes DLT (checkpoint_chaincode.go, CheckpointContract.sol), SIEM (siem_factory.py), and self-evolution (evolution.py).
Part of the unified platform.



Workflow:

RCG generates artifacts from a README (main.py).
OmniCore serializes outputs and routes them to SFE via message bus (start_workflow → sfe_workflow).
SFE analyzes, fixes, and optimizes code, storing checkpoints (CheckpointContract.sol).


Getting Started

⚡ **Quick Start**: See [QUICKSTART.md](./QUICKSTART.md) for a 5-minute setup guide.
📦 **Deployment**: See [DEPLOYMENT.md](./DEPLOYMENT.md) for production deployment instructions.

Prerequisites

- **OS**: Linux, macOS, or Windows 10/11
- **Python**: 3.11+ (required - Python 3.10 and below are not supported)
- **Docker & Docker Compose**: For containerized deployment (recommended)
- **Make**: For simplified commands (optional but recommended)
- **Git**: For version control

Dependencies: Install via requirements.txt for each component:
```
pip install -r requirements.txt
```

> **Note**: Python 3.11+ is required. Earlier versions are not supported due to dependency requirements.


API Keys: At least one LLM provider:

xAI Grok (recommended)
OpenAI
Google Gemini
Anthropic Claude
Local LLM (Ollama)


Optional: Redis, Kafka, PostgreSQL, Fabric/EVM nodes, SIEM integration.
Hardware: 8GB RAM, 4-core CPU minimum (16GB/8-core recommended for SFE simulations).

Installation
Quick Start (Recommended)
The fastest way to get started using Make and Docker:

# Clone repository
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2

# Initial setup (creates .env file)
make setup

# Edit .env with your API keys
nano .env  # or use your favorite editor

# Start all services with Docker
make docker-up

# Access services:
# - Generator API: http://localhost:8000
# - OmniCore API: http://localhost:8001
# - Grafana: http://localhost:3000
# - Prometheus: http://localhost:9090


See [QUICKSTART.md](./QUICKSTART.md) for detailed instructions.
Manual Installation
For development without Docker:

Clone Repository:
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2


Create Environment Configuration:
cp .env.example .env
# Edit .env and add your API keys


Install Dependencies:
# Install all dependencies for the unified platform
make install-dev

# Or manually:
pip install --upgrade pip
pip install -r requirements.txt


Start Redis (required):
# Using Docker
docker run -d -p 6379:6379 redis:7-alpine

# Or install locally
# macOS: brew install redis && brew services start redis
# Ubuntu: sudo apt-get install redis-server


Run Services:
# Terminal 1 - Generator
make run-generator

# Terminal 2 - OmniCore
make run-omnicore


Setup DLT (optional, for checkpoint_chaincode.go, CheckpointContract.sol):

Deploy Hyperledger Fabric test network:
./network.sh up  # From Fabric samples


Deploy EVM contract on Ethereum/Polygon:
npx hardhat deploy --network <network>





Configuration

Environment Variables: Copy .env.example to .env and configure:
cp .env.example .env


Key variables to configure in .env:
# Application
APP_ENV=development  # or production
DEBUG=true

# LLM API Keys (add at least one)
GROK_API_KEY=your-grok-api-key
OPENAI_API_KEY=your-openai-api-key

# Infrastructure
REDIS_URL=redis://localhost:6379
DATABASE_URL=sqlite:///./dev.db

# Security
SECRET_KEY=your-secret-key
JWT_SECRET_KEY=your-jwt-secret

# Observability
LOG_LEVEL=INFO
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317


Component Configuration:

Generator: Configure generator/config.yaml with LLM providers
OmniCore: Set omnicore_engine/config.yaml for message bus and database
SFE: Update self_fixing_engineer/agent_orchestration/crew_config.yaml:
version: 10.0.0
id: self_fixing_engineer_crew
agents:
  - id: refactor
    name: Refactor Agent
    agent_type: ai
    compliance_controls:
      - id: AC-6
        status: enforced



Environment Variables

See .env.example for all available configuration options.

Core Variables:

APP_ENV: production or development (default: development)
REDIS_URL: Redis backend for mesh/event_bus.py
CREW_CONFIG_PATH: Path to crew_config.yaml
AUDIT_LOG_PATH: Path for audit logs
CHECKPOINT_BACKEND_TYPE: fs, s3, or fabric for checkpoints

API Keys:

GROK_API_KEY: xAI Grok API key
OPENAI_API_KEY: OpenAI API key
GOOGLE_API_KEY: Google Gemini API key
ANTHROPIC_API_KEY: Anthropic Claude API key

Observability:

PROMETHEUS_MULTIPROC_DIR: Prometheus metrics directory
OTEL_EXPORTER_OTLP_ENDPOINT: OpenTelemetry collector endpoint
LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)


Usage
CLI Usage
Trigger a workflow with a README:
cd omnicore_engine
python -m omnicore_engine.cli --code-factory-workflow --input-file ../input_readme.md

# Or using the Makefile
make run-cli

Sample Input README:
# Flask To-Do App
- REST API: `/todo` (POST, {"task": "string"}), `/todos` (GET, JSON array).
- In-memory storage.
- Port: 8080.
- Include Dockerfile, tests, docs.

Output: app.py, test_app.py, Dockerfile, README.md in omnicore_engine/output.
API Usage
Start FastAPI server:
# Using Make
make run-omnicore

# Or manually
cd omnicore_engine
python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 --reload

Trigger workflow via API:
curl -X POST http://localhost:8000/code-factory-workflow \
-H "Content-Type: application/json" \
-d '{"requirements": "Create a Flask app with /todo endpoint"}'

View API documentation:
# Generator API docs
http://localhost:8000/docs

# OmniCore API docs
http://localhost:8001/docs

Demo Workflow

Prepare Input: Save a README at input_readme.md in the project root.
Run CLI: python -m omnicore_engine.cli --code-factory-workflow --input-file input_readme.md
Check Outputs: Verify output/ for artifacts.
Monitor SFE: SFE analyzes and fixes code, logs events to audit_trail.log.

Or run the demo:
cd generator
python demo_investor.py


Makefile Commands
The platform includes a comprehensive Makefile for common tasks:
Development:
make help              # Show all available commands
make setup             # Initial setup for new developers
make install           # Install production dependencies
make install-dev       # Install with development tools
make run-generator     # Run Generator service
make run-omnicore      # Run OmniCore Engine

Testing:
make test              # Run all tests
make test-generator    # Test Generator only
make test-omnicore     # Test OmniCore only
make test-sfe          # Test Self-Fixing Engineer only
make test-coverage     # Run tests with coverage report

Code Quality:
make lint              # Run all linters
make format            # Format code with Black
make type-check        # Run type checking
make security-scan     # Run security scans
make ci-local          # Run all CI checks locally

Docker:
make docker-build      # Build Docker images
make docker-up         # Start all services
make docker-down       # Stop all services
make docker-logs       # View logs
make docker-clean      # Clean Docker resources

Maintenance:
make clean             # Clean generated files and caches
make clean-all         # Deep clean (includes Docker and databases)
make health-check      # Check service health

See Makefile for all available commands.


Extending Code Factory
Custom Plugins
Add a plugin to D:\Code_Factory\self_fixing_engineer\plugins:
# my_plugin.py
from omnicore_engine.plugin_registry import register, PlugInKind
async def my_task(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"result": "processed"}
register(kind=PlugInKind.CORE_SERVICE, name="my_plugin", version="1.0.0")(my_task)

Update core.py to load:
self.plugin_registry.load_plugins_from_dir(str(Path("D:/Code_Factory/self_fixing_engineer/plugins")))

Custom Agents
Add an agent to D:\Code_Factory\self_fixing_engineer\agent_orchestration:
# my_agent.py
from crew_manager import CrewAgentBase
class MyAgent(CrewAgentBase):
    async def run(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": "done"}
CrewManager.register_agent_class(MyAgent)

Update crew_config.yaml:
agents:
  - id: my_agent
    name: My Agent
    agent_type: ai
    entrypoint: run

DLT and SIEM Integrations

DLT: Configure checkpoint_chaincode.go or CheckpointContract.sol in configs/config.json:"checkpoint_backend": {"type": "fabric", "url": "fabric://localhost"}


SIEM: Add to siem_factory.py:class MySIEMClient(SIEMBase):
    async def log(self, event: Dict[str, Any]):
        pass
SIEMFactory.register("my_siem", MySIEMClient)




Key Components

RCG (D:\Code_Factory\Generator):
main.py: CLI/GUI entrypoint.
agents/codegen_agent.py: Code generation with LLMs.
agents/testgen_agent.py: Test generation with pytest, hypothesis.
security_utils.py: PII redaction, encryption.


OmniCore (D:\Code_Factory\omnicore_engine):
sharded_message_bus.py: Event routing.
plugin_registry.py: Plugin management.
database.py: SQLAlchemy persistence.


SFE (D:\Code_Factory\self_fixing_engineer):
arbiter.py: Orchestrates Arbiter AI.
bug_manager.py: Bug remediation.
checkpoint_chaincode.go: Fabric DLT checkpointing.
envs/code_health_env.py: RL optimization.




Tests

Test Locations:

Generator: generator/tests/
OmniCore: omnicore_engine/tests/
SFE: self_fixing_engineer/tests/, test_generation/tests/


Run Tests:
# Run all tests
make test

# Run specific component tests
make test-generator
make test-omnicore
make test-sfe

# Run with coverage
make test-coverage

# Or manually
pytest -v generator/tests/
pytest -v omnicore_engine/tests/
pytest -v self_fixing_engineer/tests/




Troubleshooting

Missing Plugins: Check core.py for correct paths.
Dependency Errors: Install requirements.txt or use bootstrap_agent_dev.py. For full dependencies, use master_requirements.txt.
Audit Log Failure: Verify AUDIT_LOG_PATH and audit_log.py.
DLT Issues: Ensure Fabric/EVM nodes are running (network.sh up).
ArrayBackend Issues: The ArrayBackend module (omnicore_engine/array_backend.py) has a known syntax error (line 1031). The system functions without it by falling back to NumPy for array operations. Advanced array backend features (CuPy, Dask, Quantum) are unavailable until this is resolved.


Best Practices

Sandboxing: Use SANDBOXED_ENV=1 for SFE simulations.
Auditing: Enable guardrails/audit_log.py for compliance.
Monitoring: Set up Prometheus/Grafana (metrics.py).
Backups: Store configs in S3 (configs/config.json).
Testing: Achieve 90%+ coverage with pytest-cov.
Environment: Always use .env for configuration, never commit secrets.
Development: Use make ci-local before committing to catch issues early.


CI/CD Pipeline
The platform includes a consolidated CI/CD pipeline using GitHub Actions with intelligent path-based filtering:

**Continuous Integration (`.github/workflows/ci.yml`)**:

- **Path-Based Filtering**: Jobs run only when relevant files change, reducing CI time and resource usage
- **Change Detection**: Automatically detects which components (Generator, OmniCore, SFE) have changed
- **Linting**: Black, Ruff, and Flake8 with strict error checking (no error suppression)
- **Component Tests**: Runs tests for Generator, OmniCore, and SFE independently
- **Integration Tests**: End-to-end platform testing
- **Docker Builds**: Automated container image builds
- **Code Coverage**: Comprehensive coverage reporting with Codecov


Security Scanning (.github/workflows/security.yml):

Dependency vulnerability scanning (safety, pip-audit)
Secret scanning (TruffleHog)
Static analysis (CodeQL, Bandit)
Docker image scanning (Trivy)
License compliance checks


Continuous Deployment (.github/workflows/cd.yml):

Automated builds on main branch
Docker image publishing to GHCR
Staging and production deployments
Rollback capabilities
Deployment notifications


Dependency Management (.github/workflows/dependency-updates.yml):

Weekly dependency update checks
Automated pull requests for updates
Outdated package reporting



Running CI Checks Locally:
```bash
# Run all CI checks (recommended before committing)
make ci-local

# Individual checks
make lint              # Black, Ruff, Flake8 (strict - will fail on errors)
make type-check        # mypy type checking (strict)
make security-scan     # Bandit, Safety (strict)
make test              # All component tests (strict)
```

> **Important**: All linting and testing commands now enforce strict checking. Errors will cause failures instead of being suppressed, ensuring code quality.

See [DEPLOYMENT.md](./DEPLOYMENT.md) for production deployment instructions.

Contribution Guidelines

Code Style: PEP 8, use black, ruff for formatting and linting.
Tests: Add to tests/ with 90%+ coverage.
Docs: Update README.md, QUICKSTART.md, and component docs.
PRs: Use feature/<name> branches, include changelog.
Pre-commit: Run make ci-local before committing.
Security: Never commit secrets, use .env files (excluded from git).


Roadmap

v1.1.0: Multi-modal UI generation (uizard integration).
v1.2.0: Grok 3 support (custom_llm_provider_plugin.py).
v2.0.0: Multi-DLT, ISO 27001 compliance, auto-scaling.
Future: Quantum-native optimization (quantum.py).


Support

Email: support@novatraxlabs.com
Issues: <enterprise-repo-url>/issues
SLA: Enterprise 24/7 support


License
Proprietary and Confidential © 2025 Novatrax Labs LLC. All rights reserved.Code Factory and Self-Fixing Engineer™ are proprietary technologies. Unauthorized copying, distribution, reverse engineering, or use is strictly prohibited. For licensing, contact support@novatraxlabs.com.

Unleash the future of software development with Code Factory’s AI-driven, self-sustaining ecosystem.

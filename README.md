<!-- Copyright 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Autonomous Software Engineer (ASE)

![CI](https://github.com/musicmonk42/The_Code_Factory_Working_V2/actions/workflows/pytest-all.yml/badge.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-Proprietary-red)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)
![Docker](https://img.shields.io/badge/docker-supported-blue?logo=docker&logoColor=white)

> **ASE v1.0.0** -- Transform natural language requirements into production-ready applications with automated, self-healing maintenance powered by AI, DLT, and multi-agent orchestration.

*Created by Brian D Anderson, Novatrax Labs LLC*

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Key Components](#key-components)
- [Testing](#testing)
- [Deployment](#deployment)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

The Autonomous Software Engineer (ASE) is an enterprise-grade platform that automates the full software development and maintenance lifecycle. Given high-level requirements (a README, a prompt, or structured YAML spec blocks), ASE generates production-ready code, tests, deployment configurations, and documentation -- then continuously maintains and improves them.

**Core capabilities:**

| Capability | Description |
|---|---|
| Automated Code Generation | Converts requirements into code, tests, Dockerfiles, Helm charts, and docs |
| Self-Healing Maintenance | Arbiter AI detects and remediates bugs, drift, and degradation automatically |
| Compliance and Security | Enforces NIST/ISO standards, PII redaction, tamper-evident audit logging |
| Distributed Ledger | Immutable checkpoint provenance via Hyperledger Fabric and EVM contracts |
| Observability | Prometheus metrics, OpenTelemetry tracing, SIEM integration |
| Multi-Agent Orchestration | AI, human, and plugin agents coordinated with RBAC and event-driven scaling |
| Self-Evolution | Reinforcement learning and genetic algorithms optimize system health over time |

---

## Architecture

ASE is a unified platform composed of three tightly integrated modules that share dependencies, a single Docker image, and a common CI/CD pipeline.

```
+------------------------------------------------------------------+
|                        ASE Unified Platform                      |
|                                                                  |
|  +------------------+  +------------------+  +-----------------+ |
|  |    Generator      |  |    OmniCore      |  |      SFE        | |
|  |    (RCG)          |  |    Engine         |  |  (Arbiter AI)   | |
|  |                   |  |                   |  |                 | |
|  | codegen_agent     |  | sharded_msg_bus   |  | arbiter.py      | |
|  | testgen_agent     |  | plugin_registry   |  | bug_manager     | |
|  | deploy_agent      |  | database (SQLAlchemy) | codebase_analyzer| |
|  | docgen_agent      |  | cli / fastapi_app |  | evolution.py    | |
|  | security_utils    |  | audit.py          |  | checkpoint_mgr  | |
|  +--------+---------+  +--------+---------+  +--------+--------+ |
|           |                      |                     |          |
|           +----------+-----------+----------+----------+          |
|                      |                      |                     |
|              +-------v-------+    +---------v---------+           |
|              |  Message Bus  |    | DLT (Fabric/EVM)  |           |
|              +-------+-------+    +---------+---------+           |
|                      |                      |                     |
|              +-------v----------------------v---------+           |
|              |  Observability (Prometheus / OTel / SIEM)          |
|              +------------------------------------------------+   |
+------------------------------------------------------------------+
```

**Workflow:** Generator produces artifacts from requirements. OmniCore serializes and routes them to SFE via the message bus. SFE analyzes, fixes, and optimizes code, storing immutable checkpoints on-chain.

For a deep dive into scalability, see [SCALABLE_ARCHITECTURE.md](./docs/SCALABLE_ARCHITECTURE.md).

---

## Quick Start

The fastest path from clone to running services:

```bash
# Clone the repository
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2

# Run initial setup (creates .env from template)
make setup

# Add at least one LLM API key to .env
nano .env

# Start all services via Docker
make docker-up
```

Once running, access:

| Service | URL |
|---|---|
| Generator API | http://localhost:8000 |
| OmniCore API | http://localhost:8001 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |

For the full walkthrough, see [QUICKSTART.md](./docs/QUICKSTART.md).

---

## Installation

### Prerequisites

- **Python** 3.10 or higher
- **Docker and Docker Compose** (recommended for production)
- **Make** (optional but recommended)
- **Git**
- **OS:** Linux, macOS, or Windows 10/11
- **Hardware:** 8 GB RAM / 4-core CPU minimum (16 GB / 8-core recommended)

### Tiered Dependencies

ASE uses tiered requirements files so you install only what you need:

| File | Scope |
|---|---|
| `requirements.txt` | Core platform dependencies |
| `requirements-ml.txt` | Machine learning extensions |
| `requirements-blockchain.txt` | DLT and blockchain integrations |
| `requirements-ai.txt` | Additional AI provider libraries |
| `requirements-pqc.txt` | Post-quantum cryptography |
| `requirements-optional.txt` | Optional utilities |

```bash
# Core only
pip install -r requirements.txt

# Add ML capabilities
pip install -r requirements-ml.txt

# Add blockchain support
pip install -r requirements-blockchain.txt
```

### Manual Installation (without Docker)

```bash
git clone https://github.com/musicmonk42/The_Code_Factory_Working_V2.git
cd The_Code_Factory_Working_V2

cp .env.example .env
# Edit .env -- add at least one LLM API key

# Install dependencies
make install-dev
# Or: pip install --upgrade pip && pip install -r requirements.txt

# Start Redis (required)
docker run -d -p 6379:6379 redis:7-alpine

# Run services
make run-generator   # Terminal 1
make run-omnicore    # Terminal 2
```

### Kafka (optional, recommended for production)

```bash
# Automated setup
./scripts/kafka-setup.sh

# Or run without Kafka in development
export KAFKA_DEV_DRY_RUN=true
```

See [KAFKA_SETUP.md](./docs/KAFKA_SETUP.md) for full configuration.

---

## Configuration

### Environment Variables

Copy the template and fill in your keys:

```bash
cp .env.example .env
```

Key variables:

```bash
# Application
APP_ENV=development          # or production
DEBUG=true

# LLM API Keys (at least one required)
GROK_API_KEY=your-key        # xAI Grok (recommended)
OPENAI_API_KEY=your-key
GOOGLE_API_KEY=your-key
ANTHROPIC_API_KEY=your-key

# Infrastructure
REDIS_URL=redis://localhost:6379
DATABASE_URL=sqlite:///./dev.db

# Security
SECRET_KEY=your-secret-key
JWT_SECRET_KEY=your-jwt-secret

# Observability
LOG_LEVEL=INFO
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

For the complete variable reference, see [ENVIRONMENT_VARIABLES.md](./docs/ENVIRONMENT_VARIABLES.md).
For secrets handling, see [SECRETS_MANAGEMENT.md](./docs/SECRETS_MANAGEMENT.md).

### Component Configuration

- **Generator:** `generator/config.yaml` (LLM providers, generation settings)
- **OmniCore:** `omnicore_engine/config.yaml` (message bus, database)
- **SFE:** `self_fixing_engineer/agent_orchestration/crew_config.yaml` (agents, compliance)

---

## Usage

### CLI

```bash
cd omnicore_engine
python -m omnicore_engine.cli --code-factory-workflow --input-file ../input_readme.md

# Or via Make
make run-cli
```

**Example input (input_readme.md):**

```markdown
# Flask To-Do App
- REST API: /todo (POST), /todos (GET)
- In-memory storage
- Port 8080
- Include Dockerfile, tests, docs
```

**Output:** `app.py`, `test_app.py`, `Dockerfile`, and `README.md` generated in `omnicore_engine/output/`.

### API

```bash
# Start the server
make run-omnicore

# Trigger a workflow
curl -X POST http://localhost:8000/code-factory-workflow \
  -H "Content-Type: application/json" \
  -d '{"requirements": "Create a Flask app with /todo endpoint"}'
```

Interactive API documentation is available at `/docs` on each service endpoint.

### Structured Spec Blocks

For deterministic generation, use YAML spec blocks instead of free-form text.
See [SPEC_BLOCK_FORMAT.md](./docs/SPEC_BLOCK_FORMAT.md) for the full specification.

---

## Key Components

### Generator (RCG) -- `generator/`

Converts requirements into production artifacts using specialized AI agents:

- `codegen_agent.py` -- Code generation via LLM providers
- `testgen_agent.py` -- Test generation (pytest, hypothesis)
- `deploy_agent.py` -- Dockerfile and Helm chart generation
- `security_utils.py` -- PII redaction and encryption

### OmniCore Engine -- `omnicore_engine/`

Central orchestration layer:

- `sharded_message_bus.py` -- Event routing between modules
- `plugin_registry.py` -- Plugin lifecycle management
- `database.py` -- SQLAlchemy persistence (SQLite dev, PostgreSQL/Citus prod)
- `cli.py` / `fastapi_app.py` -- CLI and REST interfaces

### Self-Fixing Engineer (SFE) -- `self_fixing_engineer/`

Autonomous maintenance powered by Arbiter AI:

- `arbiter.py` -- Orchestrates analysis, fix, and optimization cycles
- `bug_manager.py` -- Automated bug detection and remediation
- `codebase_analyzer.py` -- Static and dynamic code analysis
- `evolution.py` -- Genetic algorithms for system health optimization
- `envs/code_health_env.py` -- Reinforcement learning environment

---

## Testing

```bash
# Run all tests
make test

# Run by component
make test-generator
make test-omnicore
make test-sfe

# Run with coverage report
make test-coverage

# Run all CI checks locally before committing
make ci-local
```

Test directories: `generator/tests/`, `omnicore_engine/tests/`, `self_fixing_engineer/tests/`.

For testing strategy and guidelines, see [TESTING.md](./docs/TESTING.md).

---

## Deployment

### Docker

```bash
make docker-build    # Build the unified image
make docker-up       # Start all services
make docker-down     # Stop all services
make docker-logs     # View logs
```

### Kubernetes

```bash
make k8s-deploy-dev       # Development
make k8s-deploy-staging   # Staging
make k8s-deploy-prod      # Production
make k8s-status           # Check deployment status
```

See [KUBERNETES_DEPLOYMENT.md](./docs/KUBERNETES_DEPLOYMENT.md) for manifests and configuration.

### Helm

```bash
make helm-install    # Install release
make helm-lint       # Lint chart
make helm-template   # Preview rendered templates
```

See [HELM_DEPLOYMENT.md](./docs/HELM_DEPLOYMENT.md) for values and chart documentation.

### CI/CD

The platform uses GitHub Actions with path-based filtering:

- **CI** -- Linting (Black, Ruff, Flake8), component tests, integration tests, Docker builds
- **Security** -- Dependency scanning, secret scanning, CodeQL, Trivy
- **CD** -- Automated builds, GHCR image publishing, staged rollouts

See [CI_CD_GUIDE.md](./docs/CI_CD_GUIDE.md) for pipeline details.

---

## Documentation

| Guide | Description |
|---|---|
| [QUICKSTART.md](./docs/QUICKSTART.md) | Get started in 5 minutes |
| [DEPLOYMENT.md](./docs/DEPLOYMENT.md) | Production deployment guide |
| [ARCHITECTURE_PLAN.md](./docs/ARCHITECTURE_PLAN.md) | System architecture and design decisions |
| [SCALABLE_ARCHITECTURE.md](./docs/SCALABLE_ARCHITECTURE.md) | Horizontal scaling, auto-scaling, performance |
| [SPEC_BLOCK_FORMAT.md](./docs/SPEC_BLOCK_FORMAT.md) | Structured YAML spec blocks for deterministic generation |
| [MANUAL_SFE_DISPATCH.md](./docs/MANUAL_SFE_DISPATCH.md) | Manual control of SFE job dispatch |
| [ENVIRONMENT_VARIABLES.md](./docs/ENVIRONMENT_VARIABLES.md) | Complete environment variable reference |
| [SECRETS_MANAGEMENT.md](./docs/SECRETS_MANAGEMENT.md) | Secret storage and rotation |
| [TESTING.md](./docs/TESTING.md) | Testing strategy and guidelines |
| [CI_CD_GUIDE.md](./docs/CI_CD_GUIDE.md) | CI/CD pipeline configuration |
| [HELM_DEPLOYMENT.md](./docs/HELM_DEPLOYMENT.md) | Helm chart deployment |
| [KUBERNETES_DEPLOYMENT.md](./docs/KUBERNETES_DEPLOYMENT.md) | Kubernetes deployment guide |
| [KAFKA_SETUP.md](./docs/KAFKA_SETUP.md) | Kafka infrastructure setup |
| [TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md) | Common issues and solutions |

---

## Contributing

1. Create a feature branch: `feature/<name>`
2. Follow PEP 8; format with `black` and lint with `ruff`
3. Add tests with 90%+ coverage
4. Run `make ci-local` before pushing
5. Never commit secrets -- use `.env` files
6. Submit a pull request with a changelog entry

---

## License

Proprietary. Copyright 2025 Novatrax Labs LLC. All rights reserved.

For licensing inquiries, contact support@novatraxlabs.com.

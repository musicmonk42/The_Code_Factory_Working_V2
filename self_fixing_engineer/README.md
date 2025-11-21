Self-Fixing Engineer (SFE) Platform

Version: 1.0.0 | Last Updated: August 22, 2025 | License: MIT

> **Quick Start**: See [QUICKSTART.md](../QUICKSTART.md) for platform-wide setup, or use `make test-sfe` from the root directory.

## Quick Commands

```bash
# From root directory
make test-sfe                # Run SFE tests
docker-compose up            # Run full platform with Docker

# From self_fixing_engineer directory
python run_sfe.py            # Run SFE workflow
python cli.py --help         # CLI help
pytest tests/ -v             # Run tests
python run_tests.py          # Run specific test suite
```

Overview
The Self-Fixing Engineer (SFE) platform is an innovative, AI-driven DevOps automation framework designed to autonomously analyze, test, fix, and optimize software systems. Built for enterprise-grade scalability, security, and reliability, SFE leverages a modular architecture to orchestrate AI agents, enforce compliance, and integrate with modern DevOps tools. Its self-healing capabilities, driven by reinforcement learning (RL), genetic algorithms, and blockchain-based audit logging, position SFE as a unique solution for reducing mean time to resolution (MTTR), improving code quality, and ensuring compliance in complex software environments.
SFE targets the high-value 2025 DevOps market, competing with tools like GitLab’s AI features, CrewAI, and Motia, by offering a unified platform for end-to-end software lifecycle automation. Key features include:

Autonomous Agent Orchestration: Dynamically manages AI, human, and plugin agents for code analysis, testing, and remediation.
Self-Healing Capabilities: Automatically resolves import issues, dependencies, and bugs using AI-driven insights.
Tamper-Evident Audit Logging: Ensures traceability with blockchain (Ethereum, Hyperledger Fabric) and SIEM integrations.
Compliance Enforcement: Supports NIST, GDPR, and SOC2 compliance with robust policy enforcement and reporting.
Extensible Plugin System: Integrates with Azure Event Grid, Kafka, PagerDuty, Slack, and more for event streaming and alerting.
Reinforcement Learning: Optimizes code health and configurations using RL environments and genetic algorithms.
Production-Ready Design: Incorporates Prometheus metrics, OpenTelemetry tracing, and secure sandboxing for enterprise use.

Project Structure
The SFE platform is organized into modular components, each with a specific role in the self-fixing ecosystem:

Arbiter: Orchestrates autonomous agents, manages policies, and supports human-in-the-loop interactions (arbiter.py, human_loop.py, policy/core.py).
Test Generation: Generates and manages tests for Python/JavaScript codebases (gen_agent/*, orchestrator/*).
Simulation: Provides sandboxed execution, parallel processing, quantum optimization, and visualization (simulation_module.py, sandbox.py, dashboard.py).
DLT Clients: Implements tamper-evident audit logging with blockchain support (dlt_evm_clients.py, dlt_fabric_clients.py).
SIEM Clients: Integrates with observability platforms like AWS CloudWatch, Azure Sentinel, and Splunk (siem_aws_clients.py, siem_azure_clients.py).
Self-Healing Import Fixer: Automates import resolution and dependency healing (analyzer/*, fixer_ai.py, fixer_dep.py).
Refactor Agent: Defines a schema-driven agent crew for refactoring and judging (refactor_agent.yaml).
Plugins: Extends functionality with integrations for Kafka, PagerDuty, Slack, and more (kafka_plugin.py, pagerduty_plugin.py).
Mesh: Provides an event-driven backbone with pub/sub and checkpoint management (event_bus.py, checkpoint_manager.py).
Agent Orchestration: Unifies agent operations with API, CLI, and web interfaces (agent_core.py, api.py, cli.py, crew_manager.py).
Guardrails: Ensures compliance and auditability (audit_log.py, compliance_mapper.py).
Envs: Supports RL-driven code health and configuration optimization (code_health_env.py, evolution.py, checkpoint_chaincode.go).
Contracts: Implements blockchain checkpointing for Fabric and EVM (checkpoint_chaincode.go, CheckpointContract.sol).
Configs: Defines project settings (config.json).
CI/CD: Automates testing and deployment (ci.yml).

Installation
Prerequisites

Python: 3.10+ (tested with 3.10.11)
Go: 1.18+ (for checkpoint_chaincode.go)
Node.js: 16+ (for JavaScript test generation)
Docker: For sandboxed execution and testing
Redis: For event bus and caching
Dependencies: Listed in requirements.txt (e.g., fastapi, pydantic, prometheus-client, deap)

Setup Instructions

Clone the Repository:
git clone https://github.com/musicmonk42/self_fixing_engineer.git
cd self_fixing_engineer


Install Python Dependencies:
pip install -r requirements.txt


Set Up Environment Variables:Create a .env file in the root directory:
cp .env.example .env

Update .env with required values (e.g., OPENAI_API_KEY, REDIS_URL, AWS_ACCESS_KEY_ID).

Build Go Chaincode (for Fabric integration):
cd fabric_chaincode
go build checkpoint_chaincode.go


Deploy Smart Contract (for EVM integration):Deploy CheckpointContract.sol using Hardhat or Foundry (requires Solidity 0.8.21).

Run Docker Containers:
docker-compose up -d


Initialize Configuration:
python config.py



Usage
Running the CLI
The main CLI (cli.py) provides an interactive interface for managing SFE workflows:
python cli.py

Available commands:

analyze: Analyzes codebases for defects and compliance.
heal: Applies AI-driven fixes for imports and dependencies.
serve: Starts the FastAPI server for API access.
selftest: Runs health checks and diagnostics.

Running the API
The FastAPI server (api.py) provides RESTful endpoints:
uvicorn api:create_app --host 0.0.0.0 --port 8000

Key endpoints:

/predict: Generates AI-driven predictions.
/health: Checks system health.
/prune_old_sessions: Prunes old sessions (requires consent).

Running the Web App
The Streamlit-based web app (web_app.py) offers a visual interface:
streamlit run web_app.py

Features:

Real-time visualization of agent actions.
Compliance and audit dashboards.
Interactive code health monitoring.

Example Workflow

Analyze a Codebase:python cli.py analyze path/to/codebase


Generate Tests:python cli.py generate-tests path/to/codebase


Apply Fixes:python cli.py heal path/to/codebase


View Results:Access the web app at http://localhost:8501 to visualize results.

Production Deployment
Requirements

Secrets Management: Use HashiCorp Vault or AWS Secrets Manager for secure key storage.
API Gateway: Deploy behind a gateway (e.g., AWS API Gateway) for SSL, authentication, and rate limiting.
Monitoring: Configure Prometheus and Grafana for metrics (policy_decisions_total, session_save_attempts_total) and OpenTelemetry for tracing.
CI/CD: Use the provided GitHub Actions workflow (ci.yml) for automated testing, linting, and deployment.
Blockchain: Deploy checkpoint_chaincode.go on a Hyperledger Fabric network and CheckpointContract.sol on an EVM-compatible blockchain.
Compliance: Ensure NIST, GDPR, and SOC2 compliance using compliance_mapper.py.

Deployment Steps

Lint and Test:
ruff check .
pytest --cov=./ --cov-report=xml


Build Docker Image:
docker build -t sfe:latest .


Deploy to Kubernetes:Use Helm charts to deploy with liveness and readiness probes:
helm install sfe ./helm/sfe


Configure Alerts:Set up Prometheus Alertmanager rules (see api.py for examples like HighAPIErrorRate).

Validate Setup:
python cli.py selftest



Production Checklist

Pin dependencies in requirements.txt.
Run vulnerability scans (pip-audit, trivy, snyk).
Configure circuit breakers and retries (tenacity, aiobreaker).
Enable audit logging with DLT/SIEM integration (audit_log.py, siem_plugin.py).
Ensure RBAC with mesh_policy.py and compliance_mapper.py.
Monitor metrics via Grafana dashboards.

Demo Setup
Prerequisites

Dockerized mock services (Redis, PostgreSQL, Splunk).
Pre-configured .env with mock credentials.
Sample codebase for analysis.

Demo Workflow

Setup Environment:
docker-compose -f docker-compose.demo.yml up -d


Run CLI Demo:
python cli.py analyze demo_codebase
python cli.py heal demo_codebase


Run API Demo:
uvicorn api:create_app --host 0.0.0.0 --port 8000
curl -X POST http://localhost:8000/predict -H "Authorization: Bearer $TOKEN" -d '{"user_input": "Hello"}'


Run Web App Demo:
streamlit run web_app.py

Access at http://localhost:8501 to view agent actions and compliance reports.

Visualize Metrics:Set up Grafana to connect to Prometheus (METRICS_PORT) and display dashboards.


Demo Highlights

Agent Orchestration: Show CrewManager (crew_manager.py) scaling agents dynamically.
Self-Healing: Demonstrate import fixes (fixer_ai.py) and dependency healing (fixer_dep.py).
Compliance: Display NIST reports (compliance_mapper.py) and audit logs (audit_log.py).
Blockchain: Simulate checkpoint writes (checkpoint_chaincode.go, CheckpointContract.sol).
RL Optimization: Run code_health_env.py to show code health improvements.

Testing
SFE includes comprehensive test suites for all modules:

Unit Tests: test_audit_log.py, test_compliance_mapper.py, test_crew_config.py, test_crew_manager.py, test_integration.py, checkpoint_chaincode_test.go.
Integration Tests: test_guardrails_integration.py, checkpoint_chaincode_integration_test.go.
Coverage: Assumed ~70% (run pytest --cov=./ to verify).

Run tests:
pytest
go test -v -cover ./fabric_chaincode

Known Limitations

Incomplete Components: agent_orchestration, ci_cd lack code.
Mocked Backends: Quantum (quantum.py), DLT (SimpleDLTClient), and SIEM (Splunk) use mocks.
Duplicated Logic: fixer_ai.py and core_ai.py overlap in Self-Healing Import Fixer.
Missing Entrypoints: main.py and smart_refactor_agent.py referenced but absent.
Security Risks: Manual key management and mocked backends.
Scalability Gaps: No Kafka consumer groups, limited checkpoint sharding.

Roadmap to Production

Timeline: 15-22 months with 8-10 engineers.
Budget: $2.8M-$4M.
Milestones:
Real blockchain/SIEM integrations (3 months).
Implement ci_cd and agent_orchestration (6 months).
Full Pinecone integration in agent_core.py (4 months).
Complete checkpoint methods (rollback, diff) in checkpoint_manager.py and checkpoint_chaincode.go (4 months).
Real-world RL metrics in code_health_env.py (5 months).
Achieve 80% test coverage (3 months).


Demo: Functional in 4-6 months, showcasing end-to-end workflows.

Contributing
See CONTRIBUTING.md for guidelines on reporting bugs, suggesting features, and submitting code changes.
Security
See SECURITY.md for vulnerability reporting and disclosure policies.
License
SFE is licensed under the MIT License. See LICENSE.md for details.
Contact

Homepage: https://github.com/musicmonk42/self_fixing_engineer
Issues: https://github.com/musicmonk42/self_fixing_engineer/issues
Email: security@self_fixing_engineer.org (for security reports)



graph TD
    A[Arbiter: Orchestrate Workflow] -->|Initiates| B[Test Generation: Create Tests]
    A -->|Coordinates| C[Simulation: Run Sandbox]
    A -->|Manages| D[Self-Healing: Fix Imports/Deps]
    A -->|Delegates| E[Refactor Agent: Apply Refactoring]
    A -->|Enforces| F[Guardrails: Ensure Compliance]
    A -->|Routes Events| I[Mesh: Manage Events]
    A -->|Coordinates| J[Agent Orchestration: Manage Agents]
    A -->|Optimizes| K[Envs: Code Health RL]
    A -->|Applies Settings| M[Configs: Project Settings]
    
    B -->|Tests| C
    C -->|Results| D
    D -->|Fixed Code| E
    E -->|Refactored Code| F
    F -->|Compliant Code| G[DLT Clients: Log Audit]
    F -->|Feedback| A
    G -->|Logs| H[SIEM Clients: Publish Logs]
    H -->|Events/Alerts| I
    I -->|Event Streaming| L[Plugins: Kafka, PagerDuty, Slack]
    I -->|Checkpoints| N[Contracts: Blockchain Checkpoints]
    J -->|Agent Status| A
    K -->|Optimization| A
    N -->|State| A
    M -->|Settings| A
    I -->|Deployments| O[CI/CD: Deploy Changes]
    O -->|Feedback| A


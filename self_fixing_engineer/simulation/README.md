# Simulation Module – Self-Fixing Engineer (SFE) 🚀

## Overview

The **Simulation module** is a core component of the Self-Fixing Engineer (SFE) platform, developed by **Unexpected Innovations Inc.** as of **September 10, 2025**. It orchestrates AI-driven simulations for autonomous code evolution, testing, and verification, with robust support for parallel execution, quantum computing, and secure sandboxing. The module integrates with SFE components like `arbiter`, `intent_capture`, `agent_orchestration`, `guardrails`, `proto`, `fabric_chaincode`, `evm_chaincode`, `envs`, and `ci_cd`, enabling enterprise-grade automation pipelines. It complies with 2025 AI regulations (e.g., EU AI Act, NIST AI RMF 2.1) through tamper-evident auditing and policy enforcement.

**Version**: 2.0 (stable)  
**SPDX-License-Identifier**: Apache-2.0  
**Copyright**: © 2025 Unexpected Innovations Inc.

### ✨ Core Features
- **Agentic Simulations**: Executes self-evolving agents (`agentic.py`) with genetic algorithms and RL (Ray RLlib).
- **Parallel Execution**: Supports multiple backends (`parallel.py`: asyncio, Ray, Dask, Kubernetes, AWS Batch).
- **Quantum Computing**: Integrates quantum operations (`quantum.py`: Qiskit, D-Wave) for advanced simulations.
- **Secure Sandboxing**: Runs code in isolated environments (`sandbox.py`) with seccomp/AppArmor, resource limits, and auditing.
- **Observability**: Provides Prometheus metrics (`metrics.py`), OpenTelemetry tracing (`otel_config.py`), and auditing (`dlt_clients/`).
- **Extensibility**: Plugin registry (`registry.py`) for custom runners (`runners.py`) and DLT clients (`dlt_clients/`).
- **Compliance**: Aligns with EU AI Act and NIST RMF via auditing and HITL integrations.

For new users: If you're new to SFE or Code Factory, start with `GETTING_STARTED.md` for basics and `DEMO_GUIDE.md` to run your first demo.

---

## Table of Contents
- [Features](#core-features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Production Deployment](#production-deployment)
- [Usage](#usage)
- [Integration Guides](#integration-guides)
- [Extending the Simulation Module](#extending-the-simulation-module)
- [Key Components](#key-components)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)
- [Security Notes](#security-notes)
- [Contribution Guidelines](#contribution-guidelines)
- [Roadmap](#roadmap)
- [Support](#support)
- [License](#license)

---

## Architecture
The module orchestrates simulations via:
- **Core Execution**: `simulation_module.py` (unified sims), `core.py` (job runner).
- **Parallel Backends**: `parallel.py` (asyncio, Ray, Kubernetes, AWS Batch).
- **Sandboxing**: `sandbox.py` (seccomp/AppArmor, Docker).
- **Plugins**: `registry.py` (dynamic plugins), `dlt_clients/` (blockchain).
- **Observability**: `metrics.py`, `otel_config.py`, `dlt_clients/dlt_base.py`.

**Submodules**:
- **agentic.py**: Self-evolving agents with genetic algorithms.
- **dashboard.py**: Streamlit dashboard for results and health.
- **explain.py**: LLM-based explanations with history management.
- **quantum.py**: Quantum simulation support (Qiskit, D-Wave).
- **runners.py**: Execution backends for scripts/containers.
- **dlt_clients/**: Blockchain clients (Ethereum, Fabric, Corda, Quorum, SimpleDLT).

---

## Getting Started
See `GETTING_STARTED.md` for detailed setup if you're new. Quick overview:

### Prerequisites
- **Python**: 3.10+ (use `pyenv`).
- **Dependencies**: `pip install -r requirements.txt` (includes `aiohttp`, `pydantic`, `ray[rllib]`, `qiskit`, etc.).
- **Services**: Redis, Postgres, Neo4j, Docker. Use Docker Compose from `DEMO_GUIDE.md`.

### Installation
1. Clone: `git clone https://github.com/unexpected-innovations/sfe.git && cd sfe/simulation`
2. Install: `pip install -r requirements.txt`
3. Run demo: See `DEMO_GUIDE.md`.

### Configuration
Edit `config.yaml` or use `.env` for secrets (e.g., `REDIS_URL`, `ENCRYPTION_KEY`).

---

## Production Deployment
- **Secrets Management**: Use AWS KMS/GCP Secrets for `ENCRYPTION_KEY`.
- **CI/CD Integration**: Use `ci_cd` module; run tests in GitHub Actions.
- **Monitoring and Alerting**: Prometheus at `:9090/metrics`; integrate with PagerDuty/Slack.

---

## Usage
- **CLI Mode**: `python -m simulation.core --mode single --config config.yaml`
- **REST API Mode**: `uvicorn parallel:app --port 8000`; docs at `/docs`.
- **Visualizing Results**: `streamlit run dashboard.py` for interactive views.
- **Monitoring and Logging**: Check `logs/`, `sandbox_audit.log`.

---

## Integration Guides
- **Kubernetes Integration**: Use `parallel.py --backend kubernetes`.
- **AWS Batch Integration**: Configure `AWS_ACCESS_KEY_ID` in `.env`.
- **DLT Auditing**: Enable in `dlt_clients/` for blockchain logs.

Example with `arbiter`:
```python
from simulation.simulation_module import run_simulation
async def integrate_with_arbiter(config):
    result = await run_simulation(config)
    # Publish to arbiter queue
    print(result)

Extending the Simulation Module

Adding a New Model: Inherit from base in simulation_module.py.
Creating Custom Scenarios: Add to scenario_definitions.py (if exists) or custom file.
Adding Plugins: Register in registry.py (see example in file).
Extending Metrics: Add to metrics.py with Prometheus gauges.


Key Components

simulation_module.py: Main entrypoint for simulations.
parallel.py: Distributed execution.
sandbox.py: Secure isolation.
dlt_clients/: Auditing clients.


Tests
Run: pytest --cov=simulation --cov-report=html.
Coverage target: 95%. Key tests in tests/ for backends, sandbox, etc.

Troubleshooting

Dependency issues: Check requirements.txt; run pip check.
Sandbox errors: Ensure Docker running; check sandbox_audit.log.
Metrics not showing: Verify Prometheus installed.


Best Practices

Use async for I/O-bound tasks.
Enable sandboxing in production.
Monitor with Prometheus/OpenTelemetry.


Security Notes

Restrict logs/configs: chmod 700 simulation_results.
Use DLT for audits: DLT_TYPE=evm.
Validate inputs with Pydantic.


Contribution Guidelines

Code Style: PEP 8, use black.
Testing: Add to tests/; aim for 95% coverage.
PRs: Run pytest and ruff check.
Security: Scan with bandit.


Roadmap

Q4 2025: Chaos testing, Grok 3 integration.
Future: Dynamic scheduling, enhanced quantum.


Support

Email: support@unexpectedinnovations.com
GitHub: <enterprise-repo-url>/issues</enterprise-repo-url>
Discord: https://discord.gg/sfe-community (new users welcome!)
Wiki: <enterprise-repo-url>/wiki</enterprise-repo-url>


License
Proprietary and Confidential © 2025 Unexpected Innovations. All rights reserved.
For licensing: support@unexpectedinnovations.com.
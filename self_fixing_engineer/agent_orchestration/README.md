<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Agent Orchestration Module – Self-Fixing Engineer (SFE) 🚀
**Agent Orchestration v1.0.0 – The "Universal Crew" Edition**  
Proprietary Technology by Novatrax Labs

Orchestrate dynamic AI, human, and plugin agents for self-healing, compliant, and scalable workflows.

Crafted with precision in Fairhope, Alabama, USA.

> **Unleash your SFE workflows with Agent Orchestration’s universal crew.**

**Version:** 1.0.0 (August 19, 2025)  
**License:** Proprietary (© 2025 Novatrax Labs)  
**Contact:** [support@novatraxlabs.com](mailto:support@novatraxlabs.com)  
**Issues:** `<enterprise-repo-url>/issues` (enterprise access required)

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
- [Usage](#usage)
  - [Managing Agents](#managing-agents)
  - [Scaling Crews](#scaling-crews)
  - [Audit Logging](#audit-logging)
  - [Compliance Checks](#compliance-checks)
  - [CLI Usage](#cli-usage)
  - [HTTP API](#http-api)
- [Extending Agent Orchestration](#extending-agent-orchestration)
- [Key Components](#key-components)
- [Tests](#tests)
  - [Running Tests](#running-tests)
  - [Test Coverage](#test-coverage)
- [Troubleshooting](#troubleshooting)
  - [Common Issues](#common-issues)
  - [Debugging Tips](#debugging-tips)
- [Best Practices](#best-practices)
- [Contribution Guidelines](#contribution-guidelines)
  - [Code Style](#code-style)
  - [Pull Requests](#pull-requests)
- [Deployment](#deployment)
  - [Containerization](#containerization)
  - [CI/CD Pipeline](#cicd-pipeline)
- [Roadmap](#roadmap)
- [Support](#support)
- [License](#license)

---

## Features

**Enterprise-grade orchestration for distributed, self-healing agent crews:**

- **Dynamic Crew Management:**  
  - Async lifecycle operations (add/start/stop/reload/scale) via `crew_manager.py`
  - Sandboxed execution (Docker, subprocess)
  - Tag-based bulk control and dynamic agent class loading

- **Compliance & Governance:**  
  - NIST 800-53 controls in `crew_config.yaml`
  - Guardrails/compliance integration (validation, gap analysis)
  - Event hooks for compliance triggers

- **Security:**  
  - RBAC via `RBAC_ROLE` and `mesh_policy.py`
  - Tamper-evident logging (audit_log.py)
  - Input validation and resource limits

- **Observability:**  
  - Structured JSON logging (rotation), Prometheus metrics, OpenTelemetry tracing
  - SIEM, Slack, and email plugin notifications

- **Scalability:**  
  - State in Redis/Postgres for recovery, DLT checkpointing
  - Backpressure and heartbeat monitoring for high availability

- **Extensibility:**  
  - Pluggable agent classes, event hooks, dynamic integrations
  - Policy store and state backend support

- **Production Readiness:**  
  - Async/sync compatibility, retries, resource monitoring
  - Documented failure modes (e.g., heartbeat timeouts)

---

## Architecture

The module is a scalable, secure, and observable core for SFE, integrating tightly with platform modules:

**Core Components:**
- `crew_manager.py`: Async orchestrator for lifecycle, scaling, health, and policy enforcement
- `crew_config.yaml`: Schema-driven config for agents, compliance, policies, and hooks

**Integrations:**
- Guardrails (audit/compliance)
- Mesh (RBAC/state)
- Plugins (SIEM, Slack, DLT)
- Environments (code health, evolution)
- CI/CD

**Artifacts:**
- `audit_trail.log`: Hash-chained audit logs
- `s3://universal-engineer-artifacts/`: Agent artifacts and configs
- `metrics/`: Prometheus endpoints

**ASCII Architecture:**
```
[crew_config.yaml] -> [crew_manager.py] -> Lifecycle (add/start/stop/scale)
                                           -> Health/Heartbeat
                                           -> Policy (mesh_policy.py)
                                           -> Audit (audit_log.py)
                                           -> Metrics (Prometheus)
                                           -> State (Redis/Postgres/DLT)
[External] -> Plugins (siem_plugin, slack_plugin)
           -> DLT (fabric_chaincode, evm_chaincode)
           -> Envs (code_health_env.py, evolution.py)
```

---

## Getting Started

### Prerequisites

- **Python:** 3.10+ (3.11+ recommended)
- **OS:** Linux/macOS preferred; Windows supported
- **Dependencies:**  
  - Core: `pyyaml`, `cerberus`, `tenacity`
  - Optional: `psutil`, `aioredis`, `prometheus_client`, `opentelemetry-api`, `fastapi`, `uvicorn`
- **Environment:** Docker/K8s (sandboxing), Redis/Postgres (state)
- **DLT:** Fabric/EVM (optional, see `fabric_chaincode.go`)

### Installation

```bash
# Clone repository (enterprise access required)
git clone <enterprise-repo-url>
cd self_fixing_engineer/agent_orchestration

# Create requirements.txt and install
cat <<EOF > requirements.txt
pyyaml==6.0.1
cerberus==1.3.4
tenacity==8.2.0
psutil==5.9.5
aioredis==2.0.1
prometheus_client==0.20.0
opentelemetry-api==1.20.0
fastapi==0.103.0
uvicorn==0.23.0
EOF

pip install -r requirements.txt

# (Optional) Set up Fabric for DLT
curl -sSL https://bit.ly/2ysbOFE | bash -s -- 2.5.0
cd fabric-samples/test-network
./network.sh up createChannel

# Verify
python -m crew_manager --health-check
```

_Use a virtualenv for development. Lint and scan with flake8/bandit. Ensure Redis is running for state persistence._

### Configuration

**`crew_config.yaml` Example:**
```yaml
version: 10.0.0
id: self_fixing_engineer_crew
name: Self-Fixing Engineer: Universal Crew ∞
agents:
  - id: agent1
    name: Refactor Agent
    manifest: agent1_manifest
    entrypoint: run_refactor
    agent_type: ai
    compliance_controls:
      - id: AC-6
        status: enforced
compliance_controls:
  AC-6:
    name: Least Privilege
    status: enforced
    required: true
policy:
  can_scale: true
  can_reload: true
access_policy:
  read: [admin, operator, monitor]
  write: [admin]
```

**Validate:**  
```bash
python -c "import yaml, cerberus; v=cerberus.Validator({...}); print(v.validate(yaml.safe_load(open('crew_config.yaml'))))"
```

### Environment Variables

- `MAX_AGENTS=100`
- `RBAC_ROLE=admin`
- `REDIS_URL=redis://localhost:6379`
- `AUDIT_LOG_PATH=audit_trail.log`

**Docker Example:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "-m", "crew_manager"]
```

---

## Usage

### Managing Agents

```python
from crew_manager import CrewManager
import asyncio

async def main():
    manager = CrewManager()
    await manager.add_agent("agent1", "MyWorkerAgent", config={"key": "value"}, tags=["compute"], caller_role="admin")
    await manager.start_agent("agent1")
    await manager.stop_agent("agent1")
asyncio.run(main())
```

### Scaling Crews

```python
await manager.scale(10, "MyWorkerAgent", tags=["compute"], caller_role="admin")
```

### Audit Logging

- Events are logged to `audit_trail.log` via audit_hook (integrates with `audit_log.py`)
- Verify: `python -m guardrails.audit_log --verify`

### Compliance Checks

```bash
python -m guardrails.compliance_mapper --config-path crew_config.yaml
```

### CLI Usage

Implement in `crew_manager.py`:
```python
if __name__ == "__main__":
    import argparse, asyncio, json
    parser = argparse.ArgumentParser(description="Crew Manager CLI")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--add-agent", nargs=3, metavar=("NAME", "CLASS", "CONFIG"))
    args = parser.parse_args()
    async def run():
        manager = CrewManager()
        if args.health_check:
            print(json.dumps(await manager.health(), indent=2))
        elif args.add_agent:
            name, cls, config = args.add_agent
            await manager.add_agent(name, cls, json.loads(config))
    asyncio.run(run())
```
**Run:**
```bash
python -m crew_manager --health-check
python -m crew_manager --add-agent agent1 MyWorkerAgent '{"key":"value"}'
```

### HTTP API

Use FastAPI:
```python
from fastapi import FastAPI
from crew_manager import CrewManager

app = FastAPI()
manager = CrewManager()

@app.get("/health")
async def health():
    return await manager.health()

@app.post("/agents/{name}")
async def add_agent(name: str, cls: str, config: dict, tags: list = []):
    return await manager.add_agent(name, cls, config, tags, caller_role="admin")
```
**Run:**  
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

_Extend endpoints for scale/stop/reload. Secure with OAuth2 or API keys._

---

## Extending Agent Orchestration

- **Custom Agents:**  
  Inherit `CrewAgentBase`, implement `process`, and register with `CrewManager.register_agent_class`.
- **Event Hooks:**  
  Add with `manager.add_hook("on_agent_start", on_start)`.
- **State Backends:**  
  Implement custom save/restore functions.
- **Policy Integration:**  
  Extend `mesh_policy.py` for custom policies.
- **Config Validation:**  
  Use Cerberus in `add_agent`.

---

## Key Components

- `crew_manager.py`: Async orchestrator for lifecycle, health, scaling, and policies
- `crew_config.yaml`: Defines agents, compliance, policies, hooks
- **Dependencies:** `psutil`, `aioredis`, `tenacity`, `prometheus_client`, `opentelemetry-api`

---

## Tests

- **Location:** `tests/`
- **Coverage Target:** 90%+
- **Unit:** `tests/test_crew_manager.py`
- **Integration:** `tests/test_integration.py`
- **Config:** `tests/test_crew_config.py`

### Running Tests

```bash
pip install pytest pytest-asyncio pytest-cov
pytest -v tests/ --cov=agent_orchestration --cov-report=html
```
- Output: `htmlcov/index.html`

### Test Coverage

- **Covered:** Agent operations, RBAC, heartbeats, Redis, logging
- **Gaps:** Edge cases (sandbox failures, Redis disconnects)
- **Add:** Load/mocking tests for scale and failure

---

## Troubleshooting

### Common Issues

- **Agent Startup:**  
  - Check sandbox_runner, Docker status (`docker ps`)
- **Audit Logging:**  
  - Verify `AUDIT_LOG_PATH`, test with `python -m guardrails.audit_log selftest`
- **Compliance Gaps:**  
  - Check `crew_config.yaml`, run compliance_mapper
- **Heartbeat Timeouts:**  
  - Adjust `heartbeat_timeout`, check agent health

### Debugging Tips

- Enable debug logs: `export APP_ENV=development`
- Check: `cat crew_manager.log`
- Health: `curl http://localhost:8000/health`
- Trace: OpenTelemetry via `OTEL_EXPORTER_OTLP_ENDPOINT`
- Add log verbosity, use `cProfile`/`pdb` as needed

---

## Best Practices

- **Sandbox:** Use Docker/K8s for agent isolation
- **State:** Enable Redis for recovery
- **Metrics:** Expose `/metrics` (Prometheus)
- **Validate:** Run Cerberus validation in CI
- **RBAC:** Set `RBAC_ROLE`, integrate mesh_policy
- **Logs:** Rotate and sanitize
- **Backup:** Store configs in Git/S3

---

## Contribution Guidelines

### Code Style

- **Format:** PEP 8, black, ruff (`black . && ruff check ..`)
- **Type hints:** mypy (`mypy .`)
- **Lint:** flake8, bandit

### Pull Requests

- **Branch:** `feature/<name>` or `bugfix/<name>`
- **Tests:** 90%+ coverage
- **Docs:** Update README, config comments
- **CI:** Use GitHub Actions and Codecov

---

## Deployment

### Containerization

```bash
docker build -t sfe-agent-orchestration:1.0.0 .
docker run -e REDIS_URL=redis://host -e RBAC_ROLE=admin -v $(pwd)/crew_config.yaml:/app/crew_config.yaml sfe-agent-orchestration:1.0.0
```

### CI/CD Pipeline

Example (GitHub Actions):
```yaml
name: Deploy
on:
  push:
    branches: [main]
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: docker/login-action@v2
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      - run: docker build -t sfe-agent-orchestration:1.0.0 .
      - run: docker push sfe-agent-orchestration:1.0.0
```

- **Deploy:** Use Helm for Kubernetes, Prometheus/Grafana for monitoring, S3 for config backup

---

## Roadmap

- **v1.1.0:** Async I/O for logging
- **v1.2.0:** Grok 3 (xAI API) integration
- **v2.0.0:** Multi-DLT, ISO 27001, auto-scaling
- **Future:** K8s-native orchestration, multi-modal AI

---

## Support

- **Email:** [support@novatraxlabs.com](mailto:support@novatraxlabs.com)
- **Issues:** `<enterprise-repo-url>/issues`
- **SLA:** Enterprise 24/7 support

---

## License

**Proprietary and Confidential © 2025 Novatrax Labs. All rights reserved.**  
Agent Orchestration and Self-Fixing Engineer™ are proprietary technologies.  
Unauthorized copying, distribution, reverse engineering, or use is strictly prohibited.

For commercial licensing or evaluation, contact [support@novatraxlabs.com](mailto:support@novatraxlabs.com).

---

_This README provides exhaustive details for developers. Keep it up to date with every feature update!_
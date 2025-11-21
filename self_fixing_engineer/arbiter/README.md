# Arbiter Module – Self-Fixing Engineer (SFE)

## Overview

The **Arbiter module** is the central orchestration, policy enforcement, and self-improvement hub of the Self-Fixing Engineer (SFE) platform—an autonomous AI system designed to analyze, repair, evolve, and govern codebases while adhering to ethical guidelines. Developed by **Novatrax Labs LLC** (as of September 10, 2025), Arbiter integrates advanced components for agent coordination, knowledge management, bug remediation, explainable reasoning, and multi-modal processing. It is compliant with 2025 AI regulations (e.g., EU AI Act, NIST AI RMF 2.1) and emphasizes transparency, auditability, and user empowerment through human-in-the-loop (HITL) integrations.

**Key Capabilities:**

- **Autonomous Evolution:** Agents compete in simulated arenas to optimize code; meta-learning drives continuous improvement.
- **Policy Governance:** Immutable constitution, dynamic policies, and compliance checks for ethical AI operations.
- **Observability & Security:** Prometheus metrics, OpenTelemetry tracing, tamper-evident auditing, and encrypted storage.
- **Extensibility:** Plugin registry for LLM adapters (OpenAI, Anthropic, Gemini, Ollama) and multi-modal processors (image/audio/video/text).
- **SFE Integration:** Connects with `intent_agent`, `mesh`, `guardrails`, `proto`, `fabric_chaincode`, `evm_chaincode`, `envs`, `ci_cd`, and `agent_orchestration`.

Arbiter unifies the SFE ecosystem, enabling self-healing AI systems for enterprise-grade software engineering.

---

## Purpose

Arbiter enables self-healing AI systems by:

- **Enforcing Ethics & Compliance:** Guided by an immutable constitution (`arbiter_constitution.py`) and reloadable policies (`policies.json`), ensuring transparency, privacy, and integrity.
- **Managing Knowledge & Growth:** Loads and validates knowledge (`knowledge_loader.py`), tracks agent evolution (`arbiter_growth`), and builds knowledge graphs (`knowledge_graph`).
- **Orchestrating Agents:** Simulates competitions in arenas (`arena.py`) for code optimization and evolution.
- **Fixing Bugs:** Analyzes codebases (`codebase_analyzer.py`) and remediates issues (`bug_manager.py`) with tools like pylint, bandit, and semgrep.
- **Integrating Feedback:** Supports HITL via `feedback.py` and `human_loop.py` for human oversight.

---

## Setup

### Prerequisites

- **OS:** Linux/macOS (Windows via WSL2 recommended for async/Redis).
- **Python:** 3.10+ (use `pyenv` for version management).
- **Dependencies:** Install via `requirements.txt`:
  ```bash
  pip install -r requirements.txt
Contents of requirements.txt:
textaiofiles==23.2.1
aiohttp==3.9.5
aiokafka==0.10.0
aiolimiter==1.1.0
aiosmtplib==2.0.2
aiosqlite==0.20.0
apscheduler==3.10.4
astropy==6.1.0
asyncpg==0.29.0
biopython==1.83
boto3==1.34.131
chess==1.10.0
circuitbreaker==2.0.0
consul==1.10.3
control==0.9.4
cryptography==42.0.8
dendropy==4.6.1
etcd3==0.12.0
fastapi==0.111.0
gymnasium==0.29.1
langchain==0.2.5
mido==1.3.2
midiutil==1.2.1
mpmath==1.3.0
networkx==3.3
numpy==1.26.4
opentelemetry-api==1.25.0
opentelemetry-sdk==1.25.0
pandas==2.2.2
prometheus_client==0.20.0
psutil==5.9.8
psycopg2==2.9.9
pubchempy==1.0.4
PuLP==2.8.0
pydantic==2.7.4
pygame==2.5.2
pyscf==2.6.0
pytest==8.2.2
pytest-asyncio==0.23.7
pytest-cov==4.1.0
python-dotenv==1.0.1
pyyaml==6.0.1
qutip==5.0.3
rdkit==2023.9.6
redis==5.0.4
scipy==1.13.1
semgrep==1.75.0
snappy==0.6.1
sqlalchemy==2.0.30
stable-baselines3==2.3.2
statsmodels==0.14.2
sympy==1.12.1
tenacity==8.3.0
toml==0.10.2
torch==2.3.1
tqdm==4.66.4
typer==0.12.3
watchdog==4.0.1

Services: Redis (port 6379), Postgres (5432), Neo4j (7474/7687), Kafka (optional, 9092). Use Docker:
bashdocker run -d -p 6379:6379 redis
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret postgres
docker run -d -p 7474:7474 -p 7687:7687 neo4j

Environment Variables: Set in .env:
textREDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql+asyncpg://user:secret@localhost/db
ENCRYPTION_KEY=your_32_byte_key_base64_encoded
OPENAI_API_KEY=sk-...
HEALTH_AUTH_TOKEN=secret_token
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
PAGERDUTY_ROUTING_KEY=your_key


Installation

Clone repo: git clone https://github.com/unexpected-innovations/sfe.git && cd sfe/arbiter
Install dependencies: pip install -r requirements.txt
Load environment: source .env
Run exploration: python run_exploration.py --mode single

Docker Setup
For reproducible environments:
dockerfileFROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "run_exploration.py", "--mode", "single"]
Build and run:
bashdocker build -t arbiter .
docker run --env-file .env -p 8000:8000 -p 9090:9090 arbiter
Use Docker Compose for services:
yamlversion: '3.8'
services:
  arbiter:
    build: .
    env_file: .env
    ports:
      - "8000:8000"
      - "9090:9090"
  redis:
    image: redis:latest
    ports:
      - "6379:6379"
  postgres:
    image: postgres:latest
    environment:
      POSTGRES_PASSWORD: secret
    ports:
      - "5432:5432"
  neo4j:
    image: neo4j:latest
    ports:
      - "7474:7474"
      - "7687:7687"

Running Arbiter

Single Arbiter: python run_exploration.py --mode single
Arena Mode: python run_exploration.py --mode arena
API Access: FastAPI endpoints at http://localhost:8000/docs (after arena mode)
Metrics: Prometheus at http://localhost:9090/metrics
Logs: Structured JSON in logs/ with PII redaction


Development
Key Files

Core: arbiter.py (agent management), arena.py (multi-agent sim), config.py (settings).
Policy: policy/core.py (enforcement), policies.json (rules).
Knowledge: knowledge_loader.py (data loading), knowledge_graph/core.py (graphs), learner/core.py (learning).
Plugins: arbiter_plugin_registry.py (registry), plugins/llm_client.py (LLM adapters), plugins/multimodal/ (multimodal processors).
Observability: metrics.py (Prometheus), otel_config.py (OpenTelemetry), audit_log.py (auditing).

Testing
Run tests with >95% coverage:
bashpytest arbiter/tests/ -v --cov=arbiter --cov-report=html
Submodule tests: pytest arbiter/policy/tests/
Extending

LLM Adapters: Add in plugins/ (inherit LLMClient, register via registry.register).
Custom Policies: Register async callables in policy/core.py.
Multimodal Processors: Implement in plugins/multimodal/providers/, register in PluginRegistry.

Observability & Security

Metrics/tracing: Extend with custom spans (otel_config.py) or metrics (metrics.py).
Security: Rotate encryption keys via policy_manager.rotate_encryption_key; audit via audit_log.py.


SFE Platform Integration
Arbiter integrates with:

intent_agent: Processes user intents, feeds tasks to decision_optimizer.py.
pythonfrom arbiter.decision_optimizer import DecisionOptimizer
async def process_intent(intent):
    optimizer = DecisionOptimizer()
    await optimizer.allocate_task({"id": "task123", "type": "code_fix", "data": intent})

mesh: Publishes events via message_queue_service.py.
pythonfrom arbiter.message_queue_service import MessageQueueService
async def publish_to_mesh():
    mq = MessageQueueService()
    await mq.publish("intent_processed", {"task": "code_fix", "id": "123"})

guardrails: Extends policies via policy_manager.py.
chaincode: Audits to blockchain via models/audit_ledger_client.py.


Known Issues (as of September 10, 2025)

Resolved in v2.0: File conflicts (e.g., multiple core.py), synchronous calls (web3/Feast).
Pending: Quantum-resistant crypto (Kyber, NIST PQC 2025); GraphRAG integration (v2.1, Q4 2025).
Testing: Prior import errors in pytest resolved; multimodal/load tests pending.

Report issues on GitHub.

Future Improvements

v2.1 (Q4 2025): GraphRAG for policy reasoning, dynamic scaling.
Scalability: Redis/Kafka sharding for high loads.
Compliance: Real-time NIST/EU AI Act dashboards.
Performance: Dynamic task intervals, ML-based PII detection.
Extensibility: FeatureHub as Feast alternative.


Community

GitHub: https://github.com/unexpected-innovations/sfe
Discord: https://discord.gg/sfe-community
Wiki: https://github.com/unexpected-innovations/sfe/wiki


License
SPDX-License-Identifier: Apache-2.0
Copyright © 2025 Novatrax Labs LLC All rights reserved.
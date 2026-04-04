# Architecture Plan

## Risk Grade: L3

### Risk Assessment
- [x] Contains security/auth logic -> L3 (JWT auth, HMAC audit signing, DLT crypto, RBAC)
- [x] Modifies existing APIs -> L2 (FastAPI REST + WebSocket surface)
- [ ] UI-only changes -> L1

**Rationale**: L3 assigned due to extensive security surface — JWT authentication, cryptographic audit signing (HMAC), DLT checkpoint contracts (EVM/Quorum), PII redaction, and compliance enforcement (NIST/ISO). Multiple auth and sandbox validation defects have been identified (see BACKLOG.md).

## File Tree (The Contract)

```
A.S.E/
|-- server/                          # FastAPI application server
|   |-- main.py                      # App entry, middleware, security
|   |-- routers/                     # REST + WebSocket endpoints
|       |-- jobs.py                  # Job CRUD (POST/GET/DELETE /jobs)
|       |-- generator.py             # Upload, clarification, run
|       |-- sfe.py                   # SFE dispatch, status, control
|       |-- api_keys.py             # API key management
|       |-- jobs_ws.py              # WebSocket job progress
|       |-- clarifier_ws.py         # WebSocket clarification Q&A
|
|-- generator/                       # README-to-App Code Generator (RCG)
|   |-- main/
|   |   |-- main.py                 # Orchestrator (CLI/API/GUI modes)
|   |   |-- cli.py                  # Click-based CLI
|   |   |-- engine.py               # WorkflowEngine
|   |-- agents/
|   |   |-- codegen_agent/          # Code generation
|   |   |-- testgen_agent/          # Test generation
|   |   |-- deploy_agent/           # Dockerfile, Helm, docker-compose
|   |   |-- docgen_agent/           # Documentation generation
|   |   |-- critique_agent/         # Validation, syntax repair
|   |-- clarifier/                  # LLM-driven clarification Q&A
|   |-- runner/                     # Logging, metrics, security utils
|   |-- audit_log/                  # Tamper-evident audit logging + crypto
|   |-- utils/llm_client.py         # Multi-provider LLM client
|
|-- omnicore_engine/                 # OmniCore Omega Pro Engine
|   |-- core.py                     # Core coordination logic
|   |-- fastapi_app.py             # OmniCore API
|   |-- cli.py                     # OmniCore CLI
|   |-- database/models.py         # SQLAlchemy ORM (PostgreSQL/SQLite)
|   |-- message_bus/               # Sharded routing, encryption, backpressure
|   |   |-- guardian.py            # Guardianship/gating
|   |-- event_store.py            # Event sourcing
|   |-- audit.py                  # Crypto audit (software/HSM/dev modes)
|
|-- self_fixing_engineer/            # SFE (powered by Arbiter AI)
|   |-- main.py                     # Entry (API/CLI/Streamlit modes)
|   |-- arbiter/
|   |   |-- arbiter.py             # Main RL/meta-learning orchestrator
|   |   |-- models/audit_ledger_client.py  # DLT provenance logging
|   |-- codebase_analyzer.py       # Static analysis
|   |-- bug_manager.py             # Bug detection + management
|   |-- intent_capture/agent_core.py  # Intent-driven fixes
|   |-- guardrails/compliance_mapper.py  # NIST/ISO compliance
|   |-- simulation/plugins/dlt_clients/  # EVM + Quorum DLT clients
|   |-- fabric_chaincode/          # Hyperledger Fabric Go contracts
|   |-- mesh/checkpoint_manager.py  # Distributed checkpointing
|
|-- shared/security/                 # Shared security utilities
|   |-- hashing.py                 # SHA-256, async streaming, LRU cache
|   |-- pii_redactor.py           # PII detection + redaction
|
|-- docs/                           # Project documentation
|-- tests/                          # Test suites
|-- k8s/                            # Kubernetes manifests
|-- helm/                           # Helm charts
|-- monitoring/                     # Prometheus, Grafana configs
```

## Interface Contracts

### Server (FastAPI)
- **Input**: HTTP requests (REST), WebSocket connections
- **Output**: JSON responses, streaming WebSocket events
- **Side Effects**: Job creation in DB, file I/O, LLM API calls, audit logging

### Generator (RCG)
- **Input**: README.md / spec file, language preference, LLM provider config
- **Output**: Generated code, tests, Dockerfiles, Helm charts, documentation
- **Side Effects**: LLM API calls (OpenAI/Anthropic/Grok/Gemini/Ollama), file writes to `uploads/{job_id}/`

### OmniCore Engine
- **Input**: Events from RCG and SFE via message bus
- **Output**: Routed messages, persisted state, audit records
- **Side Effects**: Database writes (PostgreSQL/SQLite), message queue publishing (Kafka/RabbitMQ/NATS/SQS)

### Self-Fixing Engineer (SFE)
- **Input**: Codebase snapshots, bug reports, compliance violations
- **Output**: Fixed code, test results, DLT checkpoint hashes
- **Side Effects**: Code modifications, DLT transactions (EVM/Quorum/Fabric), Prometheus metrics

## Data Flow

```
README.md (input)
  -> [FastAPI] POST /jobs -> job_id
  -> [Upload] POST /api/generator/{job_id}/upload -> language detection
  -> [OmniCore] message bus dispatch
  -> [Clarifier] LLM Q&A (if needed) -> user responds
  -> [CodeGen] LLM API -> parse -> security scan -> save
  -> [TestGen] pytest/jest tests
  -> [DeployGen] Dockerfile, Helm, docker-compose
  -> [DocGen] API docs, README
  -> [Critique] validation, syntax repair
  -> [SFE] (optional) Arbiter meta-learning -> bug fixes -> DLT checkpoint
  -> [Audit] HMAC-signed tamper-proof log -> PostgreSQL + DLT
  -> [Response] job.output_files, status=COMPLETED
```

## Dependencies

| Package | Justification | Vanilla Alternative |
|---------|---------------|---------------------|
| FastAPI + Uvicorn | Async REST/WebSocket server | No |
| SQLAlchemy + Alembic | ORM + migrations (PostgreSQL/SQLite) | No |
| openai, anthropic, google-generativeai | Multi-LLM provider support | No |
| web3 | EVM/Quorum DLT checkpoint contracts | No |
| aiokafka, pika, nats-py | Multi-queue message bus | No |
| redis | Caching, distributed locks, pub/sub | No |
| prometheus_client, opentelemetry-sdk | Observability (metrics + tracing) | No |
| structlog, python-json-logger | Structured audit logging | No |
| neo4j | Knowledge graph (Arbiter learning) | No |
| chromadb | Vector DB for RAG context | No |
| sentry-sdk | Error tracking (optional) | No |

## Section 4 Razor Pre-Check
- [ ] All planned functions <= 40 lines (existing codebase exceeds this in many areas)
- [ ] All planned files <= 250 lines (existing codebase exceeds this in many areas)
- [ ] No planned nesting > 3 levels (existing codebase has deep nesting in some areas)

**Note**: This is a genesis bootstrap of an existing, mature codebase. The razor pre-check reflects current state; refactoring toward these targets is a backlog concern, not a bootstrap blocker.

---
*Blueprint sealed. Awaiting GATE tribunal.*

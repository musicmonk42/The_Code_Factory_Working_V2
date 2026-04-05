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
|-- server/                              # FastAPI application server
|   |-- main.py                          # App entry, middleware, security
|   |-- config.py                        # Server configuration
|   |-- config_utils.py                  # Configuration helpers
|   |-- dependencies.py                  # FastAPI dependency injection
|   |-- distributed_lock.py             # Distributed locking primitives
|   |-- environment.py                   # Environment detection
|   |-- logging_config.py               # Structured logging setup
|   |-- persistence.py                   # Persistence layer abstraction
|   |-- storage.py                       # File storage backends
|   |-- run.py                           # Uvicorn launch script
|   |-- routers/                         # REST + WebSocket endpoints (13 routers)
|   |   |-- jobs.py                      # Job CRUD (POST/GET/DELETE /jobs)
|   |   |-- generator.py                # Upload, clarification, run
|   |   |-- sfe.py                      # SFE dispatch, status, control
|   |   |-- api_keys.py                 # API key management
|   |   |-- jobs_ws.py                  # WebSocket job progress
|   |   |-- clarifier_ws.py            # WebSocket clarification Q&A
|   |   |-- audit.py                    # Audit log query endpoints
|   |   |-- diagnostics.py             # Health / diagnostics endpoints
|   |   |-- events.py                   # Event streaming endpoints
|   |   |-- files.py                    # File upload / download
|   |   |-- fixes.py                    # SFE fix result endpoints
|   |   |-- omnicore.py                 # OmniCore proxy endpoints
|   |   |-- v1_compat.py               # v1 backward-compatibility shim
|   |-- schemas/                         # Pydantic request/response models
|   |   |-- common.py                   # Shared schema primitives
|   |   |-- events.py                   # Event schemas
|   |   |-- fixes.py                    # Fix result schemas
|   |   |-- generator_schemas.py        # Generator request/response
|   |   |-- jobs.py                     # Job schemas
|   |   |-- omnicore_schemas.py         # OmniCore schemas
|   |   |-- sfe_schemas.py             # SFE schemas
|   |-- middleware/                       # ASGI middleware
|   |   |-- arbiter_policy.py           # Arbiter policy enforcement
|   |   |-- tracing.py                  # OpenTelemetry tracing middleware
|   |-- services/                        # Business logic layer (decomposed)
|   |   |-- service_context.py          # Shared service context / DI container
|   |   |-- job_router.py              # Job routing / dispatch decisions
|   |   |-- generator_service.py        # Generator orchestration service
|   |   |-- sfe_service.py             # SFE orchestration service
|   |   |-- sfe_dispatch_service.py     # SFE async dispatch
|   |   |-- dispatch_service.py         # General dispatch service
|   |   |-- omnicore_service.py         # OmniCore integration service
|   |   |-- audit_query_service.py      # Audit log query service
|   |   |-- diagnostics_service.py      # Diagnostics / health service
|   |   |-- admin_service.py            # Admin operations service
|   |   |-- message_bus_service.py      # Message bus integration service
|   |   |-- job_finalization.py         # Job completion / cleanup
|   |   |-- sfe_utils.py               # SFE utility functions
|   |   |-- helpers/                    # Shared service helpers
|   |   |   |-- validation.py          # Input validation helpers
|   |   |   |-- project_detection.py   # Language/framework detection
|   |   |   |-- _templates.py          # Response templates
|   |   |   |-- fallback_generators.py # Fallback code generators
|   |   |   |-- sfe_cache.py           # SFE result caching
|   |   |   |-- file_utils.py          # File I/O helpers
|   |   |-- pipeline/                   # Generator pipeline services
|   |   |   |-- pipeline_orchestrator.py  # Pipeline coordination
|   |   |   |-- codegen_service.py     # Code generation service
|   |   |   |-- deploy_service.py      # Deploy artifact service
|   |   |   |-- quality_service.py     # Quality / critique service
|   |   |-- clarifier/                  # Clarifier session services
|   |       |-- session_manager.py     # Session lifecycle
|   |       |-- question_generator.py  # LLM question generation
|   |       |-- response_processor.py  # User response processing
|   |       |-- _prompt_builder.py     # Prompt construction
|   |       |-- _response_parser.py    # Response parsing
|   |-- utils/                           # Server utilities
|   |   |-- agent_loader.py            # Lazy agent loading
|   |   |-- agent_dependency_graph.py  # Agent dependency resolution
|   |   |-- import_monitor.py          # Import health monitoring
|   |   |-- lazy_import.py             # Lazy import infrastructure
|   |   |-- omnicore.py                # OmniCore client helpers
|   |-- tests/                          # Server test suite (25+ test files)
|
|-- generator/                           # README-to-App Code Generator (RCG)
|   |-- main/
|   |   |-- main.py                     # Orchestrator (CLI/API/GUI modes)
|   |   |-- cli.py                      # Click-based CLI
|   |   |-- engine.py                   # WorkflowEngine
|   |   |-- gui.py                      # Streamlit GUI
|   |   |-- post_materialize.py         # Post-generation hooks
|   |   |-- provenance.py              # Output provenance tracking
|   |   |-- spec_integration.py        # Spec-to-engine bridge
|   |   |-- validation.py              # Input validation
|   |-- agents/
|   |   |-- codegen_agent/              # Code generation
|   |   |-- testgen_agent/              # Test generation
|   |   |-- deploy_agent/               # Dockerfile, Helm, docker-compose
|   |   |   |-- plugins/               # Deploy plugins (docker, helm, k8s, docs)
|   |   |-- docgen_agent/              # Documentation generation
|   |   |-- critique_agent/            # Validation, syntax repair
|   |   |-- generator_plugin_wrapper.py # Agent plugin adapter
|   |   |-- metrics_utils.py           # Agent metrics helpers
|   |   |-- plugin_stubs.py            # Plugin interface stubs
|   |-- clarifier/                      # LLM-driven clarification Q&A
|   |-- runner/                         # Execution runtime + LLM clients
|   |   |-- llm_client.py              # Multi-provider LLM client
|   |   |-- llm_plugin_manager.py      # LLM plugin lifecycle
|   |   |-- llm_provider_base.py       # Provider base class
|   |   |-- providers/                  # LLM provider implementations
|   |   |   |-- claude_provider.py     # Anthropic Claude
|   |   |   |-- gemini_provider.py     # Google Gemini
|   |   |   |-- grok_provider.py       # xAI Grok
|   |   |   |-- ai_provider.py         # OpenAI
|   |   |   |-- local_provider.py      # Ollama / local models
|   |   |-- runner_core.py             # Core runner logic
|   |   |-- runner_config.py           # Runner configuration
|   |   |-- runner_security_utils.py   # Security utilities
|   |   |-- runner_metrics.py          # Metrics collection
|   |   |-- runner_audit.py            # Audit integration
|   |   |-- (10 more runner modules)   # Logging, file utils, parsers, etc.
|   |-- audit_log/                      # Tamper-evident audit logging + crypto
|   |   |-- audit_log.py              # Core audit logger
|   |   |-- audit_backend/            # Backend adapters (file, SQL, cloud, streaming)
|   |   |-- audit_crypto/             # Crypto ops, keystore, factory
|   |-- intent_parser/                  # README intent extraction
|   |   |-- intent_parser.py          # Main parser
|   |   |-- question_loop.py          # Interactive Q&A
|   |   |-- spec_block.py             # Spec block model
|   |-- specs/                          # Compliance spec templates
|   |   |-- gdpr.py                    # GDPR compliance template
|   |   |-- hipaa.py                   # HIPAA compliance template
|   |   |-- router.py                  # Spec routing logic
|   |-- deterministic.py               # Deterministic output helpers
|   |-- arbiter_bridge.py              # SFE bridge adapter
|
|-- omnicore_engine/                     # OmniCore Omega Pro Engine
|   |-- core.py                         # Core coordination logic
|   |-- fastapi_app.py                 # OmniCore API
|   |-- cli.py                         # OmniCore CLI
|   |-- engines.py                     # Engine registry
|   |-- database/
|   |   |-- database.py               # DB session management
|   |   |-- models.py                  # SQLAlchemy ORM (PostgreSQL/SQLite)
|   |   |-- metrics_helpers.py         # DB metrics helpers
|   |-- message_bus/                   # Sharded routing, encryption, backpressure
|   |   |-- sharded_message_bus.py    # Core sharded bus
|   |   |-- guardian.py               # Guardianship/gating
|   |   |-- backpressure.py           # Backpressure control
|   |   |-- encryption.py             # Message encryption
|   |   |-- dead_letter_queue.py      # DLQ handling
|   |   |-- hash_ring.py             # Consistent hashing
|   |   |-- rate_limit.py            # Rate limiting
|   |   |-- resilience.py            # Circuit breaker / retry
|   |   |-- integrations/            # Kafka, Redis bridge adapters
|   |-- event_store.py               # Event sourcing
|   |-- audit.py                     # Crypto audit (software/HSM/dev modes)
|   |-- meta_supervisor.py           # Meta-supervision orchestration
|   |-- plugin_registry.py           # Plugin lifecycle management
|   |-- plugin_base.py               # Plugin base class
|   |-- plugin_event_handler.py      # Plugin event dispatch
|   |-- scenario_plugin_manager.py   # Scenario plugin orchestration
|   |-- security_config.py           # Security configuration
|   |-- security_integration.py      # Security middleware integration
|   |-- security_production.py       # Production security hardening
|   |-- security_utils.py            # Security utility functions
|   |-- secrets_manager.py           # Secrets management
|   |-- sharding.py                  # Shard assignment logic
|   |-- migrations/                  # Alembic DB migrations
|   |-- tests/                       # OmniCore test suite (35+ test files)
|
|-- self_fixing_engineer/               # SFE (powered by Arbiter AI)
|   |-- main.py                         # Entry (API/CLI/Streamlit modes)
|   |-- cli.py                          # Click CLI
|   |-- config.py                       # SFE configuration
|   |-- exceptions.py                   # SFE exception hierarchy
|   |-- evolution.py                    # Evolutionary strategies
|   |-- prompt_registry.py             # Prompt template registry
|   |-- security_audit.py             # Security audit runner
|   |-- arbiter/                        # Arbiter AI core
|   |   |-- arbiter.py                 # Main RL/meta-learning orchestrator
|   |   |-- codebase_analyzer.py       # Static analysis
|   |   |-- decision_optimizer.py      # Decision optimization
|   |   |-- explorer.py               # Code exploration
|   |   |-- file_provenance.py        # File-level provenance
|   |   |-- file_watcher.py           # Filesystem watcher
|   |   |-- human_loop.py             # Human-in-the-loop gating
|   |   |-- knowledge_loader.py       # Knowledge base loading
|   |   |-- bug_manager/              # Bug detection + management
|   |   |   |-- bug_manager.py        # Core bug manager
|   |   |   |-- remediations.py       # Auto-remediation strategies
|   |   |   |-- notifications.py      # Bug notifications
|   |   |   |-- audit_log.py          # Bug audit trail
|   |   |   |-- utils.py              # Bug manager utilities
|   |   |-- models/                    # Data models + DB clients
|   |   |   |-- audit_ledger_client.py # DLT provenance logging
|   |   |   |-- feature_store_client.py # Feature store client
|   |   |   |-- knowledge_graph_db.py  # Neo4j client
|   |   |   |-- merkle_tree.py        # Merkle tree verification
|   |   |   |-- postgres_client.py    # PostgreSQL client
|   |   |   |-- redis_client.py       # Redis client
|   |   |   |-- (3 more model modules)
|   |   |-- learner/                   # Meta-learning subsystem
|   |   |   |-- core.py               # Learner core logic
|   |   |   |-- encryption.py         # Learning data encryption
|   |   |   |-- explanations.py       # Explainable decisions
|   |   |   |-- fuzzy.py              # Fuzzy matching
|   |   |   |-- validation.py         # Learning validation
|   |   |   |-- audit.py              # Learner audit trail
|   |   |   |-- metrics.py            # Learner metrics
|   |   |-- explainable_reasoner/      # Explainable AI reasoning
|   |   |   |-- explainable_reasoner.py # Core reasoner
|   |   |   |-- prompt_strategies.py  # Prompt strategies
|   |   |   |-- history_manager.py    # Reasoning history
|   |   |   |-- (5 more modules)
|   |   |-- knowledge_graph/           # Knowledge graph subsystem
|   |   |   |-- core.py               # Graph core
|   |   |   |-- multimodal.py         # Multi-modal graph ops
|   |   |   |-- prompt_strategies.py  # Graph prompt strategies
|   |   |   |-- config.py             # Graph configuration
|   |   |   |-- utils.py              # Graph utilities
|   |   |-- meta_learning_orchestrator/ # Meta-learning coordination
|   |   |   |-- orchestrator.py       # Main orchestrator
|   |   |   |-- clients.py            # ML service clients
|   |   |   |-- models.py             # ML data models
|   |   |   |-- config.py             # ML config
|   |   |   |-- (4 more modules)
|   |   |-- arbiter_growth/            # Growth / evolution subsystem
|   |   |   |-- arbiter_growth_manager.py # Growth manager
|   |   |   |-- pqc_signing.py        # Post-quantum signing
|   |   |   |-- storage_backends.py   # Growth storage
|   |   |   |-- (5 more modules)
|   |   |-- plugins/                   # Arbiter LLM plugins
|   |   |   |-- llm_client.py         # LLM client wrapper
|   |   |   |-- openai_adapter.py     # OpenAI adapter
|   |   |   |-- anthropic_adapter.py  # Anthropic adapter
|   |   |   |-- gemini_adapter.py     # Gemini adapter
|   |   |   |-- ollama_adapter.py     # Ollama adapter
|   |   |   |-- multi_modal_plugin.py # Multi-modal plugin
|   |   |   |-- multimodal/           # Multi-modal providers
|   |-- test_generation/                # Test generation subsystem
|   |   |-- gen_agent/                 # Test gen AI agent
|   |   |   |-- agents.py             # Agent definitions
|   |   |   |-- graph.py              # Agent graph
|   |   |   |-- runtime.py            # Agent runtime
|   |   |   |-- atco_signal.py        # ATCO signaling
|   |   |   |-- cli.py                # Agent CLI
|   |   |   |-- api.py                # Agent API
|   |   |   |-- io_utils.py           # I/O utilities
|   |   |-- orchestrator/             # Test orchestration
|   |   |   |-- orchestrator.py       # Main test orchestrator
|   |   |   |-- pipeline.py           # Test pipeline
|   |   |   |-- cli.py                # Orchestrator CLI
|   |   |   |-- reporting.py          # Test reporting
|   |   |   |-- venvs.py              # Virtual environment management
|   |   |   |-- (4 more modules)
|   |   |-- backends.py               # Test runner backends
|   |   |-- compliance_mapper.py      # Compliance-to-test mapping
|   |   |-- fix_tests.py              # Auto-fix failing tests
|   |   |-- gen_plugins.py            # Test gen plugins
|   |   |-- onboard.py                # Test onboarding
|   |   |-- policy_and_audit.py       # Test policy enforcement
|   |   |-- utils.py                  # Test gen utilities
|   |-- self_healing_import_fixer/      # Self-healing import resolution
|   |   |-- cli.py                     # Import fixer CLI
|   |   |-- analyzer/                  # Import analysis engine
|   |   |   |-- analyzer.py           # Main analyzer
|   |   |   |-- core_ai.py            # AI-assisted analysis
|   |   |   |-- core_graph.py         # Dependency graph analysis
|   |   |   |-- core_policy.py        # Policy enforcement
|   |   |   |-- core_security.py      # Security scanning
|   |   |   |-- core_audit.py         # Audit logging
|   |   |   |-- core_report.py        # Report generation
|   |   |   |-- core_secrets.py       # Secrets detection
|   |   |   |-- core_utils.py         # Analyzer utilities
|   |   |-- import_fixer/             # Fix engine
|   |       |-- import_fixer_engine.py # Main fix engine
|   |       |-- fixer.py              # Core fixer logic
|   |       |-- fixer_ai.py           # AI-assisted fixes
|   |       |-- fixer_ast.py          # AST-based fixes
|   |       |-- fixer_dep.py          # Dependency fixes
|   |       |-- fixer_plugins.py      # Fixer plugins
|   |       |-- fixer_validate.py     # Fix validation
|   |       |-- cache_layer.py        # Fix caching
|   |       |-- compat_core.py        # Compatibility layer
|   |-- plugins/                        # SFE integration plugins (18 sub-dirs)
|   |   |-- azure_eventgrid_plugin/   # Azure Event Grid
|   |   |-- ci_cd/                    # CI/CD pipeline integration
|   |   |-- dlt_backend/              # DLT backend adapter
|   |   |-- ethics/                   # Ethical AI guardrails
|   |   |-- healer/                   # Self-healing plugin
|   |   |-- human/                    # Human-in-the-loop plugin
|   |   |-- judge/                    # Code quality judge
|   |   |-- kafka/                    # Kafka integration
|   |   |-- oracle/                   # Oracle / decision plugin
|   |   |-- pagerduty_plugin/         # PagerDuty alerting
|   |   |-- pubsub_plugin/            # Google Pub/Sub
|   |   |-- rabbitmq_plugin/          # RabbitMQ integration
|   |   |-- refactor/                 # Automated refactoring
|   |   |-- siem_plugin/              # SIEM integration
|   |   |-- simulation/               # Simulation plugin
|   |   |-- slack_plugin/             # Slack notifications
|   |   |-- sns_plugin/               # AWS SNS
|   |-- envs/                           # RL environments
|   |   |-- code_health_env.py        # Code health RL environment
|   |   |-- evolution.py              # Evolutionary RL strategies
|   |   |-- metrics_collector.py      # Environment metrics
|   |-- intent_capture/                 # Intent-driven fixes
|   |   |-- agent_core.py             # Intent agent core
|   |   |-- engine.py                 # Intent engine
|   |   |-- cli.py                    # Intent CLI
|   |   |-- api.py                    # Intent API
|   |   |-- session.py                # Session management
|   |   |-- spec_utils.py             # Spec utilities
|   |   |-- (5 more modules)
|   |-- guardrails/                     # Compliance guardrails
|   |   |-- compliance_mapper.py       # NIST/ISO compliance mapping
|   |   |-- audit_log.py              # Guardrail audit trail
|   |-- simulation/                     # Simulation framework
|   |   |-- core.py                    # Simulation core
|   |   |-- sandbox.py                # Sandboxed execution
|   |   |-- agent_core.py             # Simulation agent
|   |   |-- agentic.py                # Agentic simulation
|   |   |-- parallel.py               # Parallel simulation runs
|   |   |-- quantum.py                # Quantum-inspired strategies
|   |   |-- registry.py               # Simulation registry
|   |   |-- runners.py                # Simulation runners
|   |   |-- plugins/                   # Simulation plugins
|   |   |   |-- dlt_clients/          # EVM, Quorum, Fabric, Corda DLT clients
|   |   |   |-- siem_clients/         # AWS, Azure, GCP, generic SIEM clients
|   |   |   |-- plugin_manager.py     # Plugin lifecycle
|   |   |   |-- (20+ runner/tool plugins)
|   |-- agent_orchestration/            # Multi-agent orchestration
|   |   |-- crew_manager.py           # Crew/team management
|   |-- fabric_chaincode/              # Hyperledger Fabric Go contracts
|   |-- mesh/                           # Distributed mesh coordination
|   |   |-- checkpoint/               # Distributed checkpointing
|   |   |   |-- checkpoint_manager.py # Checkpoint orchestrator
|   |   |   |-- checkpoint_backends.py # Storage backends
|   |   |   |-- checkpoint_utils.py   # Checkpoint utilities
|   |   |-- event_bus.py              # Mesh event bus
|   |   |-- mesh_adapter.py           # Mesh adapter
|   |   |-- mesh_policy.py            # Mesh routing policy
|   |   |-- graph_rag_policy.py       # Graph-RAG routing
|
|-- shared/                              # Shared cross-module utilities
|   |-- security/                       # Security utilities
|   |   |-- hashing.py                 # SHA-256, async streaming, LRU cache
|   |   |-- pii_redactor.py           # PII detection + redaction
|   |-- circuit_breaker.py             # Circuit breaker pattern
|   |-- noop_metrics.py               # No-op metrics (test/dev)
|   |-- noop_tracing.py               # No-op tracing (test/dev)
|   |-- plugin_registry_base.py       # Base plugin registry
|   |-- registry.py                    # Service registry
|   |-- stubs/                         # Test stubs
|       |-- audit_stubs.py            # Audit stub implementations
|       |-- llm_stubs.py              # LLM stub implementations
|       |-- security_stubs.py         # Security stub implementations
|
|-- config/                              # Runtime configuration files
|   |-- allowlist.json                  # Allowed operations allowlist
|   |-- openapi_schema.json            # OpenAPI schema definition
|   |-- policies.json                  # Security / governance policies
|   |-- plugins.json                   # Plugin registry manifest
|
|-- docs/                               # Project documentation
|-- tests/                              # Integration / E2E test suites
|-- k8s/                                # Kubernetes manifests
|-- helm/                               # Helm charts
|-- monitoring/                         # Prometheus, Grafana configs
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
- [x] All planned files <= 250 lines (server/services/ decomposition complete -- 9 domain services, helpers/, pipeline/, clarifier/ all under 250 LOC)
- [ ] No planned nesting > 3 levels (existing codebase has deep nesting in some areas)

**Note**: This is a genesis bootstrap of an existing, mature codebase. Significant decomposition has been completed: the server layer now separates routers (13), schemas (7), middleware (2), services (9 domain + 3 sub-packages), and utils (5). The SFE arbiter has been decomposed into 10+ sub-packages (bug_manager/, learner/, explainable_reasoner/, knowledge_graph/, meta_learning_orchestrator/, arbiter_growth/, plugins/). New subsystems added: test_generation/ (gen_agent + orchestrator), self_healing_import_fixer/ (analyzer + import_fixer), envs/ (RL environments), and 18 integration plugins. Remaining razor violations are tracked in BACKLOG.md.

---
*Blueprint sealed. Awaiting GATE tribunal.*

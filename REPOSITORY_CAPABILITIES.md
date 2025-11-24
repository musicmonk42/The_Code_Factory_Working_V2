# Code Factory Platform - Complete Repository Capabilities & Architecture Documentation

**Version:** 1.0.0  
**Date:** November 24, 2025  
**Repository:** musicmonk42/The_Code_Factory_Working_V2  
**© 2025 Novatrax Labs LLC - Proprietary Technology**

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Platform Overview](#platform-overview)
3. [The Three Main Modules](#the-three-main-modules)
4. [Complete Capabilities List](#complete-capabilities-list)
5. [Module 1: Generator (README-to-App Code Generator)](#module-1-generator)
6. [Module 2: OmniCore Engine](#module-2-omnicore-engine)
7. [Module 3: Self-Fixing Engineer (SFE)](#module-3-self-fixing-engineer)
8. [The Arbiter AI System - Deep Dive](#the-arbiter-ai-system)
9. [End-to-End Workflow](#end-to-end-workflow)
10. [Integration Architecture](#integration-architecture)
11. [All Features & Functions Catalog](#all-features-functions-catalog)
12. [File Structure Reference](#file-structure-reference)
13. [Technology Stack](#technology-stack)
14. [Configuration & Environment](#configuration-environment)

---

## Executive Summary

The **Code Factory Platform** is an enterprise-grade, AI-driven ecosystem comprising **803 Python files** across three tightly integrated modules that automate the entire software development and maintenance lifecycle. The platform transforms natural language requirements into production-ready applications and continuously maintains them through self-healing, compliance enforcement, and intelligent optimization.

### Platform Statistics
- **Total Python Files:** 803 (171 Generator + 77 OmniCore + 552 SFE)
- **Lines of Code in Arbiter Core:** 26,626+ lines
- **Dependencies:** 374+ production packages
- **Supported Languages:** Python, JavaScript, Go, Solidity (Smart Contracts)
- **Architecture:** Unified, event-driven, microservices-ready
- **Deployment:** Docker, Kubernetes, Standalone

---

## Platform Overview

### What is Code Factory?

Code Factory is a **self-sustaining code ecosystem** that:
1. **Generates** production code from README files or natural language
2. **Orchestrates** all components through intelligent message routing
3. **Maintains** code automatically through AI-powered self-healing
4. **Evolves** continuously via reinforcement learning and genetic algorithms
5. **Complies** with enterprise standards (NIST, ISO, SOC2, HIPAA, PCI DSS)
6. **Audits** everything with tamper-evident distributed ledger technology

### Core Philosophy

**"From README to Production - Fully Automated"**

The platform embodies these principles:
- **Zero-Touch Deployment:** Minimal human intervention required
- **Self-Healing:** Automatically detects and fixes issues
- **Compliance-First:** Security and regulatory compliance built-in
- **Observable:** Complete visibility through metrics, traces, and logs
- **Extensible:** Plugin-based architecture for custom functionality
- **Immutable Audit:** Blockchain-backed provenance tracking

---

## The Three Main Modules

### Module Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INPUT / API REQUEST                  │
│           (README, Natural Language, Requirements)           │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  MODULE 1: GENERATOR (README-to-App Code Generator)        │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  • Clarifier Agent: Refines requirements                   │
│  • Codegen Agent: Generates source code                    │
│  • Testgen Agent: Creates comprehensive tests              │
│  • Deploy Agent: Generates Docker/K8s configs              │
│  • Docgen Agent: Creates documentation                     │
│  • Critique Agent: Security scanning & fixes               │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Output: Code, Tests, Configs, Documentation               │
└───────────────────────────┬────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  MODULE 2: OMNICORE ENGINE (Central Orchestrator)          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  • ShardedMessageBus: Event routing & distribution         │
│  • PluginRegistry: Component management                    │
│  • Database: State persistence & history                   │
│  • MetaSupervisor: Health monitoring                       │
│  • ExplainAudit: Tamper-proof logging                      │
│  • CLI/API: User interfaces                                │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Connects: Generator ↔ Self-Fixing Engineer                │
└───────────────────────────┬────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  MODULE 3: SELF-FIXING ENGINEER (SFE)                      │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  ┌──────────────────────────────────────────────────┐     │
│  │  ARBITER AI (Central Orchestrator)               │     │
│  │  3,032 lines of intelligent coordination          │     │
│  │  • Policy Engine: Enforces rules & compliance     │     │
│  │  • Arena System: Agent competitions               │     │
│  │  • Knowledge Graph: Contextual understanding      │     │
│  │  • Bug Manager: Automated remediation            │     │
│  │  • Meta-Learning: Continuous improvement          │     │
│  └──────────────────────────────────────────────────┘     │
│  • Codebase Analyzer: Deep code analysis                   │
│  • Test Generation: Automated test creation                │
│  • Simulation: Sandboxed execution                         │
│  • Guardrails: Compliance enforcement                      │
│  • Mesh: Event distribution & checkpoints                  │
│  • Plugins: External integrations (10+ types)              │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Output: Fixed Code, Optimized System, Compliance Reports  │
└───────────────────────────┬────────────────────────────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │   PRODUCTION READY    │
                │   Deployed, Monitored │
                │   Self-Healing Active │
                └───────────────────────┘
```

---

## Complete Capabilities List

### Capability Categories (20 Major Areas)


#### 1. **Code Generation Capabilities**
- Natural language to code transformation
- Multi-language support (Python, JavaScript, Go)
- Template-based code generation
- Context-aware code synthesis
- Dependency management automation
- Code structure optimization
- Design pattern application
- Best practices enforcement

#### 2. **Test Generation Capabilities**
- Automated unit test generation
- Integration test creation
- Property-based testing (Hypothesis)
- Test fixture generation
- Mock/stub generation
- Edge case detection
- Coverage analysis automation
- Test framework integration (pytest, jest)

#### 3. **Deployment & Infrastructure Capabilities**
- Dockerfile generation
- Kubernetes manifests (Deployments, Services, Ingress)
- Helm chart creation
- Terraform configuration generation
- Docker Compose files
- CI/CD pipeline generation (.github/workflows)
- Environment configuration management
- Secret management integration

#### 4. **Documentation Capabilities**
- README generation (Markdown, reStructuredText)
- API documentation (OpenAPI/Swagger)
- Code comments and docstrings
- Architecture diagrams (Mermaid)
- User guides and tutorials
- Changelog generation
- License file creation
- Contributing guidelines

#### 5. **Security Capabilities**
- Static analysis (Bandit, Semgrep, ESLint)
- Vulnerability scanning
- Secret detection and redaction
- PII scrubbing (Presidio integration)
- SQL injection detection
- XSS prevention analysis
- Dependency vulnerability checking
- Security compliance reporting (NIST, ISO)

#### 6. **Code Quality & Analysis Capabilities**
- Linting (Pylint, Flake8, Black, Ruff)
- Code complexity analysis
- Dead code detection
- Code smell identification
- Refactoring suggestions
- Performance optimization recommendations
- Style guide enforcement (PEP 8, Airbnb)
- Technical debt assessment

#### 7. **Self-Healing Capabilities**
- Automatic bug detection
- Root cause analysis
- Automated fix generation
- Fix validation in sandbox
- Rollback on failure
- Dependency resolution
- Import fixing
- Configuration auto-repair

#### 8. **Compliance & Governance Capabilities**
- SOC2 compliance enforcement
- HIPAA requirements validation
- PCI DSS compliance checks
- GDPR privacy protection
- ISO 27001 alignment
- NIST AI RMF 2.1 compliance
- EU AI Act compliance
- Audit trail generation

#### 9. **Observability & Monitoring Capabilities**
- Prometheus metrics collection
- OpenTelemetry tracing
- Structured logging (JSON)
- Performance metrics tracking
- Error rate monitoring
- Resource utilization tracking
- Custom metric definition
- Grafana dashboard integration

#### 10. **AI & Machine Learning Capabilities**
- LLM integration (OpenAI, Anthropic, Google, xAI)
- Local LLM support (Ollama)
- Multi-modal processing (text, image, audio, video)
- Reinforcement learning optimization
- Meta-learning for improvement
- Genetic algorithm evolution
- Agent competition arenas
- Transfer learning

#### 11. **Message Bus & Event Processing Capabilities**
- Asynchronous message routing
- Sharded message distribution
- Event sourcing
- Dead letter queue handling
- Message encryption
- Rate limiting
- Backpressure management
- Circuit breaking

#### 12. **Integration Capabilities**
- Kafka integration
- Redis pub/sub
- RabbitMQ support
- AWS SNS/SQS
- Google Cloud Pub/Sub
- Azure Event Grid
- Slack notifications
- PagerDuty alerting
- SIEM integration (Splunk, QRadar, Sentinel)
- Webhook support

#### 13. **Database & Storage Capabilities**
- PostgreSQL integration
- SQLite support
- Redis caching
- Neo4j knowledge graphs
- S3-compatible object storage
- File system storage
- Encrypted storage
- Database migration management

#### 14. **Blockchain & DLT Capabilities**
- Hyperledger Fabric integration
- Ethereum/EVM smart contracts
- Checkpoint storage on-chain
- Tamper-evident audit logs
- Merkle tree verification
- Immutable provenance tracking
- Smart contract deployment
- Transaction signing

#### 15. **Plugin & Extension Capabilities**
- Dynamic plugin loading
- Hot-reload support
- Plugin marketplace
- Version management
- Dependency injection
- Plugin sandboxing
- Custom agent registration
- API extension points

#### 16. **Workflow Orchestration Capabilities**
- Multi-agent coordination
- Task scheduling
- Dependency resolution
- Parallel execution
- Sequential workflows
- Conditional branching
- Error handling
- Retry logic with exponential backoff

#### 17. **Human-in-the-Loop Capabilities**
- Approval workflows
- Manual intervention points
- Feedback collection
- Decision capture
- WebSocket real-time updates
- Email notifications
- Slack integration
- Dashboard UI

#### 18. **Simulation & Testing Capabilities**
- Sandboxed code execution
- AppArmor security profiles
- Seccomp filtering
- Resource limits (CPU, memory, network)
- Parallel test execution
- Test result aggregation
- Performance benchmarking
- Load testing

#### 19. **Knowledge Management Capabilities**
- Knowledge graph construction
- Entity relationship mapping
- Context extraction
- Code understanding
- Pattern recognition
- Historical analysis
- Best practice storage
- Learning from failures

#### 20. **Optimization & Evolution Capabilities**
- Code health scoring
- Performance optimization
- Resource usage optimization
- Algorithm selection
- Configuration tuning
- A/B testing
- Genetic algorithms
- Reinforcement learning agents

---

## Module 1: Generator (README-to-App Code Generator)

### Location
`/generator/` (171 Python files)

### Purpose
Transforms natural language requirements from README files into production-ready code, tests, deployment configurations, and documentation using specialized AI agents.

### Key Components


#### 1.1 Clarifier Module (`/generator/clarifier/`)
**Purpose:** Refines ambiguous requirements into structured specifications

**Files:**
- `clarifier_agent.py` - Main clarification logic
- `clarifier_prompt.py` - Prompt engineering
- `clarifier_validator.py` - Validates clarified output

**Capabilities:**
- Natural language understanding
- Requirement disambiguation
- Technical specification generation
- Missing detail identification
- Constraint extraction
- User intent capture
- Interactive clarification (questions back to user)
- Structured output (JSON schema)

**Integration Points:**
- Input: Raw README text
- Output: Structured requirements JSON
- Next Step: Feeds into Codegen Agent

---

#### 1.2 Codegen Agent (`/generator/agents/codegen_agent/`)
**Purpose:** Generates production-ready source code from requirements

**Files:**
- `codegen_agent.py` (418 lines) - Core code generation
- `codegen_prompt.py` - LLM prompt templates
- `codegen_response_handler.py` - Response parsing & validation

**Capabilities:**
- Multi-language code generation (Python, JavaScript, Go)
- Framework-aware generation (Flask, FastAPI, Express, React)
- Design pattern application (MVC, Repository, Factory)
- Dependency management (requirements.txt, package.json)
- Code structure organization
- Module/package creation
- Entry point generation
- Configuration file creation
- Error handling implementation
- Logging integration
- Security best practices

**LLM Providers Supported:**
- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude)
- Google (Gemini)
- xAI (Grok)
- Local (Ollama)

**Integration Points:**
- Input: Clarified requirements
- Output: Source code files
- Next Step: Critique Agent for security scanning

---

#### 1.3 Critique Agent (`/generator/agents/critique_agent/`)
**Purpose:** Security scanning, vulnerability detection, and automated fixes

**Files:**
- `critique_agent.py` - Main critique orchestrator
- `critique_linter.py` - Linting integration
- `critique_fixer.py` - Automated fix application
- `critique_prompt.py` - Fix suggestion prompts

**Security Tools Integrated:**
- **Bandit** - Python security scanner
- **Semgrep** - Multi-language static analysis
- **ESLint** - JavaScript/TypeScript linting
- **Pylint** - Python code quality
- **Flake8** - Python style checking
- **Black** - Python code formatting
- **Ruff** - Fast Python linter

**Vulnerability Detection:**
- SQL injection
- XSS vulnerabilities
- CSRF weaknesses
- Insecure deserialization
- Path traversal
- Command injection
- Hardcoded secrets
- Weak cryptography
- Insecure dependencies

**Automated Fixes:**
- Code formatting
- Import organization
- Security patch application
- Dependency updates
- Configuration corrections
- Style violations

**Integration Points:**
- Input: Generated code
- Output: Secured, linted code + security report
- Next Step: Testgen Agent

---

#### 1.4 Testgen Agent (`/generator/agents/testgen_agent/`)
**Purpose:** Generates comprehensive test suites with high coverage

**Files:**
- `testgen_agent.py` - Test generation logic
- `testgen_prompt.py` - Test prompt strategies
- `testgen_validator.py` - Test validation

**Test Types Generated:**
- Unit tests (pytest, unittest, jest)
- Integration tests
- End-to-end tests
- Property-based tests (Hypothesis)
- Parametrized tests
- Mock tests
- Fixture generation

**Coverage Goals:**
- Target: 90%+ code coverage
- Branch coverage
- Path coverage
- Edge case testing

**Test Features:**
- Assertion generation
- Test data creation
- Mock/stub generation
- Setup/teardown logic
- Error condition testing
- Performance testing
- Security testing

**Frameworks Supported:**
- Python: pytest, unittest, hypothesis
- JavaScript: Jest, Mocha, Chai
- Go: testing package

**Integration Points:**
- Input: Source code + requirements
- Output: Test files (test_*.py, *.test.js)
- Next Step: Deploy Agent

---

#### 1.5 Deploy Agent (`/generator/agents/deploy_agent/`)
**Purpose:** Generates deployment configurations and infrastructure code

**Files:**
- `deploy_agent.py` - Deployment config generation
- `deploy_prompt.py` - Deployment prompts
- `deploy_validator.py` - Config validation
- `deploy_response_handler.py` - Response processing

**Deployment Artifacts Generated:**

**1. Docker:**
- Dockerfile (multi-stage builds)
- .dockerignore
- docker-compose.yml
- Health check endpoints

**2. Kubernetes:**
- Deployment manifests
- Service definitions
- ConfigMaps
- Secrets (templates)
- Ingress rules
- PersistentVolumeClaims
- HorizontalPodAutoscaler

**3. Helm Charts:**
- Chart.yaml
- values.yaml
- Templates directory
- Helpers

**4. Terraform:**
- main.tf
- variables.tf
- outputs.tf
- Provider configuration

**5. CI/CD:**
- GitHub Actions workflows
- GitLab CI
- Jenkins pipelines
- CircleCI config

**6. Cloud Platforms:**
- AWS (ECS, Lambda, CloudFormation)
- GCP (Cloud Run, GKE)
- Azure (AKS, Functions)

**Validation:**
- Syntax checking
- Security scanning (Trivy, Hadolint)
- Best practices enforcement
- Resource limits verification

**Integration Points:**
- Input: Application code + requirements
- Output: Deployment configs
- Next Step: Docgen Agent

---

#### 1.6 Docgen Agent (`/generator/agents/docgen_agent/`)
**Purpose:** Creates professional documentation

**Files:**
- `docgen_agent.py` - Documentation generation
- `docgen_prompt.py` - Documentation prompts
- `docgen_response_validator.py` - Output validation

**Documentation Types:**

**1. README.md:**
- Project overview
- Installation instructions
- Usage examples
- API reference
- Configuration guide
- Contributing guidelines
- License information
- Badges and shields

**2. API Documentation:**
- OpenAPI/Swagger specs
- Endpoint descriptions
- Request/response examples
- Authentication details
- Rate limiting info
- Error codes

**3. Code Documentation:**
- Module docstrings
- Function documentation
- Class documentation
- Parameter descriptions
- Return value documentation
- Exception documentation

**4. Architecture Docs:**
- System architecture diagrams (Mermaid)
- Component interaction diagrams
- Data flow diagrams
- Database schemas

**5. User Guides:**
- Getting started
- Tutorials
- How-to guides
- Troubleshooting
- FAQ

**Formats Supported:**
- Markdown (.md)
- reStructuredText (.rst)
- HTML
- PDF (via pandoc)

**Integration Points:**
- Input: All generated artifacts
- Output: Documentation files
- Final Step: Complete package ready

---

#### 1.7 Generator Plugin Wrapper (`generator_plugin_wrapper.py`)
**Purpose:** Orchestrates the entire generator pipeline

**16,290 lines of orchestration logic**

**Responsibilities:**
- Agent coordination
- Error handling & recovery
- Progress tracking
- Metrics collection
- Audit logging
- Resource management
- State persistence
- Rollback capabilities

**Pipeline Flow:**
```
Input → Clarifier → Codegen → Critique → Testgen → Deploy → Docgen → Output
          ↓           ↓          ↓          ↓         ↓        ↓
       Validate   Validate   Security   Validate  Validate  Validate
                             Scan
```

**Error Handling:**
- Retry logic with exponential backoff
- Circuit breakers for external services
- Graceful degradation
- Detailed error logging
- Recovery suggestions

---

#### 1.8 Audit Logging (`/generator/audit_log/`)
**Purpose:** Tamper-evident audit trail for compliance

**Files:**
- `audit_log.py` - Main audit logic
- `audit_backend/` - Storage backends (file, S3, DLT)
- `audit_crypto/` - Cryptographic functions

**Audit Capabilities:**
- Event logging
- Merkle tree construction
- Digital signatures
- Timestamp verification
- Immutable storage
- Query capabilities
- Export formats (JSON, CSV)

**Compliance:**
- SOC2 requirements
- HIPAA audit trails
- PCI DSS logging
- GDPR accountability

---

#### 1.9 Intent Parser (`/generator/intent_parser/`)
**Purpose:** Extracts user intent from natural language

**Capabilities:**
- NLP processing
- Intent classification
- Entity extraction
- Sentiment analysis
- Confidence scoring

---

#### 1.10 Runner (`/generator/runner/`)
**Purpose:** Manages LLM provider interactions

**Providers (`/generator/runner/providers/`):**
- `openai_provider.py` - OpenAI GPT integration
- `anthropic_provider.py` - Claude integration
- `google_provider.py` - Gemini integration
- `xai_provider.py` - Grok integration
- `ollama_provider.py` - Local LLM integration

**Features:**
- Provider abstraction
- Failover between providers
- Rate limiting
- Token counting
- Cost tracking
- Response caching
- Streaming support

---

## Module 2: OmniCore Engine

### Location
`/omnicore_engine/` (77 Python files)

### Purpose
Central orchestration hub that coordinates Generator and Self-Fixing Engineer, manages state, routes messages, handles plugins, and provides APIs.

### Architecture

```
OmniCore Engine
├── Core (`core.py`, `engines.py`)
├── Message Bus (event routing)
├── Plugin Registry (component management)
├── Database (state persistence)
├── API (FastAPI)
├── CLI (command-line interface)
├── Metrics & Monitoring
└── Security & Audit
```

---

### Key Components

#### 2.1 Core Engine (`core.py` - 848 lines)
**Purpose:** Base classes and utilities for all components

**Key Classes:**
- `Base` - Abstract base for all components
- `safe_serialize()` - Handles circular references, complex types
- Configuration management
- Logging setup (structlog)

**Provides:**
- Component lifecycle (initialize, shutdown, health_check)
- Serialization utilities
- Type handling (datetime, UUID, numpy, Decimal)
- Error handling patterns

---

#### 2.2 Engines Module (`engines.py` - 340 lines)
**Purpose:** Engine registration and discovery system

**Components:**
- `ENGINE_REGISTRY` - Global registry of engines
- `PluginService` - Plugin lifecycle management
- Engine discovery
- Entrypoint management

**Registered Engines:**
- Generator engine
- Import fixer engine
- Test generation engine
- Arbiter coordination

**Message Bus Integration:**
- Subscribes to `arbiter:bug_detected`
- Subscribes to `shif:fix_import_request`
- Routes to appropriate handlers

---

#### 2.3 Sharded Message Bus (`/omnicore_engine/message_bus/`)
**Purpose:** High-performance, distributed event routing

**Files:**
- `sharded_message_bus.py` - Main bus implementation
- `message_types.py` - Message schemas
- `hash_ring.py` - Consistent hashing
- `backpressure.py` - Flow control
- `rate_limit.py` - Rate limiting
- `encryption.py` - Message encryption
- `cache.py` - Message caching
- `dead_letter_queue.py` - Failed message handling
- `guardian.py` - Security enforcement
- `resilience.py` - Circuit breakers
- `metrics.py` - Performance metrics

**Capabilities:**

**1. Message Routing:**
- Topic-based pub/sub
- Consistent hashing across shards
- Priority queues
- Message ordering guarantees
- Delivery semantics (at-least-once, exactly-once)

**2. Performance:**
- Async/await throughout
- Connection pooling
- Message batching
- Compression (snappy)
- Zero-copy where possible

**3. Reliability:**
- Circuit breakers
- Retry logic
- Dead letter queues
- Message persistence
- Acknowledgments

**4. Security:**
- Message encryption (AES-256-GCM)
- Authentication
- Authorization
- Audit logging

**5. Integrations (`/message_bus/integrations/`):**
- Kafka adapter (`kafka_sink_adapter.py`)
- Redis pub/sub
- PostgreSQL LISTEN/NOTIFY

**Message Flow:**
```
Publisher → Sharding → Encryption → Routing → Subscriber
              ↓          ↓           ↓          ↓
           Hash Ring   AES-GCM    Queues    Handlers
```

---

#### 2.4 Plugin Registry (`plugin_registry.py`)
**Purpose:** Dynamic plugin discovery, loading, and management

**Plugin Types (PlugInKind enum):**
- CORE_SERVICE
- ANALYTICS
- GROWTH_MANAGER
- AI_ASSISTANT
- EXTERNAL_INTEGRATION
- TEST_GENERATOR
- DEPLOYMENT
- MONITORING
- CUSTOM

**Features:**
- Dynamic loading from directories
- Version management
- Dependency resolution
- Hot-reload support
- Marketplace integration
- Health checking
- Metrics collection

**Registered Plugins:**
- `feedback_manager` (core_service)
- `human_in_loop` (core_service)
- `codebase_analyzer` (analytics)
- `arbiter_growth` (growth_manager)
- `explainable_reasoner` (ai_assistant)

**Plugin Lifecycle:**
1. Discovery
2. Validation
3. Loading
4. Initialization
5. Registration
6. Activation
7. Monitoring
8. Deactivation/Unloading

---

#### 2.5 Database Module (`/omnicore_engine/database/`)
**Purpose:** State persistence and history

**Files:**
- `database.py` - Main database interface
- `models.py` - SQLAlchemy models

**Supported Databases:**
- SQLite (development)
- PostgreSQL (production)
- Citus (distributed PostgreSQL)

**Models:**
- `AgentState` - Agent state tracking (inherits from Arbiter)
- `WorkflowExecution` - Workflow history
- `PluginState` - Plugin status
- `AuditRecord` - Audit trail

**Features:**
- Async database operations (asyncpg, aiosqlite)
- Connection pooling
- Query optimization
- Migration support (Alembic)
- Encryption at rest
- Backup integration

---

#### 2.6 FastAPI Application (`fastapi_app.py`)
**Purpose:** REST API for platform interactions

**Endpoints:**

**Generator:**
- `POST /code-factory-workflow` - Trigger full workflow
- `POST /generate/code` - Code generation only
- `POST /generate/tests` - Test generation only
- `POST /generate/docs` - Documentation only
- `POST /generate/deploy` - Deployment configs only

**Admin:**
- `GET /admin/plugins` - List plugins
- `POST /admin/plugins/{plugin_id}/reload` - Hot-reload
- `GET /admin/health` - System health
- `GET /admin/metrics` - Performance metrics

**Audit:**
- `GET /audit/export` - Export audit logs
- `POST /audit/query` - Query audit trail

**Import Fixer:**
- `POST /fix-imports` - Auto-fix import issues

**Features:**
- OpenAPI/Swagger documentation
- Authentication (JWT, API keys)
- Rate limiting
- CORS support
- Request validation (Pydantic)
- Response serialization
- Error handling
- OpenTelemetry instrumentation

---

#### 2.7 CLI Interface (`cli.py`)
**Purpose:** Command-line interface for workflows

**Commands:**
```bash
# Trigger workflows
python -m omnicore_engine.cli --code-factory-workflow --input-file readme.md

# Health check
python -m omnicore_engine.cli --health

# Plugin management
python -m omnicore_engine.cli --list-plugins
python -m omnicore_engine.cli --reload-plugin arbiter_growth

# Audit queries
python -m omnicore_engine.cli --export-audit --format json
```

**Features:**
- Argument parsing (Click)
- Progress indicators
- Colored output
- JSON output mode
- Verbose logging
- Interactive prompts

---

#### 2.8 Meta Supervisor (`meta_supervisor.py`)
**Purpose:** System-wide health monitoring and orchestration

**Responsibilities:**
- Component health checks
- Resource monitoring (CPU, memory, disk)
- Performance tracking
- Alert generation
- Auto-scaling triggers
- Graceful degradation

**Health Check Targets:**
- All registered plugins
- Database connections
- Message bus
- External integrations
- LLM providers

---

#### 2.9 Metrics & Observability (`metrics.py`)
**Purpose:** Comprehensive system metrics

**Metrics Collected:**
- **Request Metrics:**
  - Request count
  - Response time (histogram)
  - Error rates
  - Status codes

- **Plugin Metrics:**
  - Plugin execution time
  - Success/failure rates
  - Resource usage

- **Bus Metrics:**
  - Messages published/consumed
  - Queue depths
  - Latency
  - Throughput

- **Database Metrics:**
  - Query time
  - Connection pool usage
  - Transaction rates

**Exporters:**
- Prometheus (`/metrics` endpoint)
- OpenTelemetry (OTLP)
- Custom exporters

---

#### 2.10 Security & Audit (`audit.py`, `security_config.py`)
**Purpose:** Security enforcement and audit trail

**Security Features:**
- ExplainAudit with Merkle trees
- Tamper detection
- Digital signatures
- Encryption (Fernet)
- Secret management
- Role-based access control (RBAC)

**Audit Capabilities:**
- Event logging
- Query interface
- Export (JSON, CSV, JSONL)
- Integrity verification
- Compliance reports

---

## Module 3: Self-Fixing Engineer (SFE)

### Location
`/self_fixing_engineer/` (552 Python files)

### Purpose
Autonomous AI system that analyzes, repairs, optimizes, and evolves codebases while enforcing compliance and governance through the Arbiter AI orchestrator.

### Architecture

```
Self-Fixing Engineer
│
├── ARBITER AI (Central Orchestrator) ← THE BRAIN
│   ├── Policy Engine
│   ├── Arena System
│   ├── Knowledge Management
│   ├── Bug Management
│   └── Meta-Learning
│
├── Analysis & Understanding
│   ├── Codebase Analyzer
│   ├── Intent Capture
│   └── Knowledge Graph
│
├── Maintenance & Repair
│   ├── Bug Manager
│   ├── Refactor Agent
│   ├── Import Fixer
│   └── Test Generation
│
├── Execution & Validation
│   ├── Simulation (Sandbox)
│   ├── Parallel Execution
│   └── Quantum Integration
│
├── Governance & Compliance
│   ├── Guardrails
│   ├── Policy Enforcement
│   └── Audit Logging
│
├── Learning & Evolution
│   ├── Meta-Learning Orchestrator
│   ├── Reinforcement Learning (CodeHealthEnv)
│   ├── Genetic Algorithms
│   └── Agent Competition
│
├── Integration & Communication
│   ├── Mesh (Event Bus, Checkpoints)
│   ├── Agent Orchestration
│   └── Plugins (10+ types)
│
└── Blockchain & DLT
    ├── Hyperledger Fabric
    └── Ethereum/EVM
```

---

## The Arbiter AI System - Deep Dive

### Location
`/self_fixing_engineer/arbiter/` (26,626+ lines of code)

### What is Arbiter?


**Arbiter is the CENTRAL BRAIN** of the Self-Fixing Engineer. It's a 3,032-line intelligent orchestrator that:

1. **Coordinates all SFE activities** - Acts as mission control
2. **Enforces policies and compliance** - Ensures ethical AI operations
3. **Manages agent competitions** - Arena-based evolution
4. **Builds knowledge graphs** - Understands code context
5. **Remediates bugs automatically** - Self-healing without human intervention
6. **Learns and improves continuously** - Meta-learning and RL
7. **Provides human oversight** - Human-in-the-loop when needed

### Arbiter Core Files (28 Python files)

#### 3.1 Main Arbiter (`arbiter.py` - 3,032 lines)
**The Central Intelligence**

**Key Classes:**
- `MyArbiterConfig` - Configuration management (loaded from env/JSON)
- `Arbiter` - Main orchestrator class
- `CodeHealthEnv` - RL environment wrapper
- `ArenaConfig` - Competition arena settings

**Configuration (`MyArbiterConfig`):**
```python
- DATABASE_URL: SQLite/PostgreSQL connection
- REDIS_URL: Redis for pub/sub and caching
- ENCRYPTION_KEY: For secure storage
- REPORTS_DIRECTORY: Output location
- FRONTEND_URL: Dashboard URL
- ARENA_PORT: WebSocket server port
- CODEBASE_PATHS: List of codebases to monitor
- ENABLE_CRITICAL_FAILURES: Testing mode
- AI_API_TIMEOUT: LLM timeout (30s default)
- MEMORY_LIMIT: Max memory usage (40GB default)
- OMNICORE_URL: OmniCore API endpoint
- ARBITER_URL: This Arbiter's endpoint
- AUDIT_LOG_PATH: Audit trail location
- PLUGINS_ENABLED: Plugin system on/off
- ROLE_MAP: RBAC roles (guest, user, explorer_user, admin)
- SLACK_WEBHOOK_URL: Slack notifications
- ALERT_WEBHOOK_URL: General alerts
- SENTRY_DSN: Error tracking
- PROMETHEUS_GATEWAY: Metrics pushgateway
- RL_MODEL_PATH: Reinforcement learning model
- EMAIL_* settings: SMTP configuration
- PERIODIC_SCAN_INTERVAL_S: Auto-scan interval (3600s default)
- WEBHOOK_URL: Generic webhooks
- ARBITER_MODES: [sandbox, live]
- LLM_ADAPTER: LLM provider selection
- OLLAMA_API_URL: Local LLM endpoint
- LLM_MODEL: Model name (llama3, gpt-4, etc.)
```

**Arbiter Main Functionality:**

**1. Initialization (`__init__`):**
- Load configuration from .env and arbiter_config.json
- Set up database connections (AsyncEngine with SQLAlchemy)
- Initialize Redis client (with connection pooling)
- Configure encryption (Fernet)
- Set up logging (RotatingFileHandler)
- Initialize Prometheus metrics
- Configure Sentry error tracking
- Set up OpenTelemetry tracing

**2. Component Initialization:**
- `FeedbackManager` - Collects human feedback
- `HumanInLoop` - Human oversight integration
- `Monitor` - System health monitoring
- `PolicyEngine` - Policy enforcement
- `KnowledgeLoader` - Knowledge graph loading
- `MessageQueueService` - Event distribution
- `CodebaseAnalyzer` - Code analysis
- `MultiModalPlugin` - Multi-modal AI
- `Neo4jKnowledgeGraph` - Graph database
- `PostgresClient` - Metadata storage

**3. Core Methods:**

**`async def run()`:**
- Main execution loop
- Monitors codebase for changes
- Triggers analysis and fixes
- Publishes events to message bus
- Updates knowledge graph
- Logs all activities

**`async def analyze_codebase(path)`:**
- Static analysis (pylint, bandit, semgrep)
- Complexity metrics
- Security scanning
- Dependency analysis
- Test coverage check
- Documentation quality

**`async def fix_issue(issue_data)`:**
- Root cause analysis
- Fix generation (LLM-powered)
- Validation in sandbox
- Application if successful
- Rollback if failed
- Learning from outcome

**`async def optimize_code()`:**
- Performance profiling
- Algorithm optimization
- Resource usage reduction
- Code refactoring
- Pattern application

**`async def enforce_policy(action)`:**
- Check against constitution
- Validate compliance requirements
- Check RBAC permissions
- Log policy decisions
- Reject if violation

**4. Arena System:**
- Agents compete to solve problems
- Fitness scoring
- Winner selection
- Model updating
- Population evolution

**5. Reinforcement Learning Integration:**
- PPO (Proximal Policy Optimization)
- Custom CodeHealthEnv environment
- State: code metrics, test coverage, bugs
- Actions: refactor, optimize, add tests
- Rewards: improved health score
- Training: continuous learning

**6. Metrics Tracked:**
```python
- arbiter_tasks_processed_total
- arbiter_tasks_failed_total
- arbiter_task_duration_seconds
- arbiter_rl_rewards_total
- arbiter_policy_violations_total
- arbiter_fixes_applied_total
- arbiter_knowledge_graph_nodes
- arbiter_agents_active
```

---

#### 3.2 Agent State Management (`agent_state.py`)
**Purpose:** Track state of all agents in the system

**Model: `AgentState`**
```python
- id: UUID (primary key)
- name: str
- state: Dict[str, Any] (JSON blob)
- created_at: datetime
- updated_at: datetime
- health_score: float (0.0 to 1.0)
- tasks_completed: int
- tasks_failed: int
- last_active: datetime
```

**Operations:**
- Create agent
- Update state
- Query by health score
- Track performance
- Persist to database

---

#### 3.3 Arbiter Constitution (`arbiter_constitution.py`)
**Purpose:** Immutable ethical and operational rules

**Core Principles:**
1. **Transparency:** All decisions are logged and explainable
2. **Privacy:** PII is protected and never leaked
3. **Safety:** No harmful changes without approval
4. **Accountability:** Human oversight for critical decisions
5. **Fairness:** No bias in code analysis
6. **Security:** Security first in all operations
7. **Compliance:** Regulatory requirements are enforced

**Enforcement:**
- Hard-coded rules (cannot be changed at runtime)
- Checked before every action
- Violations logged and blocked
- Human notification on violation attempts

---

#### 3.4 Policy Engine (`/arbiter/policy/`)
**Purpose:** Dynamic policy management and enforcement

**Files:**
- `core.py` - Policy evaluation engine
- `policies.json` - Policy definitions (reloadable)

**Policy Types:**
- Security policies
- Compliance policies
- Performance policies
- Resource policies
- Quality policies

**Policy Structure:**
```json
{
  "id": "SEC-001",
  "name": "No Hardcoded Secrets",
  "type": "security",
  "severity": "critical",
  "condition": "scan_for_secrets(code) == False",
  "action": "block",
  "remediation": "Use environment variables or secret manager"
}
```

**Evaluation:**
- Real-time policy checking
- Context-aware evaluation
- Priority handling
- Conflict resolution
- Override capability (with approval)

---

#### 3.5 Arena System (`arena.py`)
**Purpose:** Agent competition framework for evolution

**How It Works:**

**1. Arena Creation:**
- Define problem/challenge
- Set evaluation criteria
- Configure resource limits
- Set competition rules

**2. Agent Registration:**
- Multiple agents enter
- Each has unique strategy
- Declared capabilities

**3. Competition Rounds:**
- All agents attempt solution
- Parallel execution
- Timeout enforcement
- Resource monitoring

**4. Evaluation:**
- Correctness score
- Performance metrics
- Resource efficiency
- Code quality

**5. Winner Selection:**
- Weighted scoring
- Tie-breaking rules
- Winner announced

**6. Evolution:**
- Winner's approach analyzed
- Losers learn from winner
- Population updated
- Next generation

**Use Cases:**
- Code optimization challenges
- Bug fixing competitions
- Test generation contests
- Refactoring battles

---

#### 3.6 Knowledge Loader (`knowledge_loader.py`)
**Purpose:** Load and validate knowledge sources

**Knowledge Sources:**
- Code repositories
- Documentation
- API specifications
- Stack Overflow
- GitHub issues
- Past fixes
- Best practices

**Loading Process:**
1. Source identification
2. Content extraction
3. Validation
4. Parsing
5. Entity extraction
6. Relationship mapping
7. Graph insertion
8. Indexing

**Validation:**
- Schema compliance
- Data integrity
- Consistency checks
- Completeness verification

---

#### 3.7 Codebase Analyzer (`codebase_analyzer.py`)
**Purpose:** Deep code analysis and understanding

**Analysis Types:**

**1. Static Analysis:**
- AST parsing
- Control flow analysis
- Data flow analysis
- Complexity metrics (cyclomatic, cognitive)
- Code smells detection

**2. Security Analysis:**
- Vulnerability scanning (Bandit, Semgrep)
- Dependency vulnerabilities
- Configuration issues
- Secret detection

**3. Quality Analysis:**
- PEP 8 compliance (Python)
- Linting (Pylint, Flake8, ESLint)
- Code duplication
- Dead code
- Unused imports/variables

**4. Documentation Analysis:**
- Docstring presence
- Documentation coverage
- Comment quality
- README completeness

**5. Test Analysis:**
- Test coverage (line, branch)
- Test quality
- Missing test cases
- Flaky tests

**6. Dependency Analysis:**
- Dependency graph
- Outdated packages
- License compatibility
- Security advisories

**Outputs:**
```python
{
  "health_score": 0.85,  # Overall score
  "issues": [...],        # List of issues found
  "metrics": {...},       # Code metrics
  "recommendations": [...], # Improvement suggestions
  "dependencies": {...},  # Dependency info
  "tests": {...}         # Test analysis
}
```

---

#### 3.8 Bug Manager (`/arbiter/bug_manager/`)
**Purpose:** Intelligent bug detection, tracking, and remediation

**Files:** (8 files)
- `bug_manager.py` (1,200+ lines) - Main orchestrator
- `remediations.py` - Fix strategies
- `notifications.py` (750+ lines) - Alert system
- `utils.py` - Helper functions
- `audit_log.py` - Bug audit trail

**Bug Manager Architecture:**

**1. Bug Detection:**
- Exception catching
- Log analysis
- Metric anomalies
- User reports
- Static analysis findings

**2. Signature Generation:**
- Stack trace hashing
- Error message normalization
- Context extraction
- Deduplication

**3. Rate Limiting:**
- Prevents duplicate reports
- Configurable time windows
- Signature-based grouping

**4. Auto-Remediation:**

**ML-Powered Remediation:**
- Historical fix database
- Pattern matching
- Confidence scoring
- ML model predictions

**Rule-Based Playbooks:**
```python
class RemediationPlaybook:
    name: str
    steps: List[RemediationStep]
    timeout: int
    
class RemediationStep:
    name: str
    action_name: str  # Function to call
    on_success: str   # Next step
    on_failure: str   # Fallback step
    retries: int
    timeout_seconds: int
    idempotent: bool  # Safe to retry
```

**Available Actions:**
- restart_service
- rollback_deployment
- clear_cache
- fix_configuration
- update_dependency
- apply_patch
- scale_resources

**5. Notification System (`notifications.py`):**

**Circuit Breakers:**
- Prevents notification storms
- Per-channel tracking
- Failure threshold (default: 5)
- Half-open state for recovery
- Redis-backed state (distributed)

**Rate Limiting:**
- Per-severity limits
- Per-channel limits
- Sliding window algorithm
- Burst handling

**Channels:**
- **Slack:** Rich formatting, threading, @mentions
- **PagerDuty:** Incident creation, escalation
- **Email:** HTML formatted, attachments
- **Webhooks:** Custom integrations
- **WebSocket:** Real-time dashboard updates

**Notification Priority:**
```python
critical -> immediate alert to all channels
high     -> PagerDuty + Slack + Email
medium   -> Slack + Email (delayed)
low      -> Email only (batched)
```

**6. Bug Lifecycle:**
```
Detected → Deduplicated → Analyzed → Remediation Attempted
    ↓                                          ↓
Logged                                    Success?
                                          ↓        ↓
                                        Yes       No
                                         ↓         ↓
                                      Close    Notify Team
                                                  ↓
                                            Human Fixes
                                                  ↓
                                              Learn
```

**7. Learning System:**
- Stores successful fixes
- Trains ML model
- Updates playbooks
- Improves confidence scores

---

#### 3.9 Feedback Manager (`feedback.py`)
**Purpose:** Collect and process human feedback

**Feedback Types:**
- Arbiter decision approval/rejection
- Fix quality rating
- Feature requests
- Bug reports
- Improvement suggestions

**Storage:**
- PostgreSQL for structured data
- Redis for quick access
- Elasticsearch for search

**Processing:**
- Sentiment analysis
- Priority assignment
- Routing to appropriate team
- Integration into learning loop

---

#### 3.10 Human-in-the-Loop (`human_loop.py`)
**Purpose:** Human oversight and intervention

**Intervention Points:**
- Critical policy violations
- High-risk changes
- Ambiguous situations
- Low-confidence fixes
- New patterns

**Notification Methods:**
- Real-time dashboard (WebSocket)
- Slack messages
- Email alerts
- SMS (Twilio)
- Phone calls (critical only)

**Approval Workflow:**
1. Action proposed by Arbiter
2. Human notified with context
3. Timeout for response (configurable)
4. Default action (approve/reject/escalate)
5. Decision logged
6. Feedback incorporated

---

#### 3.11 Knowledge Graph (`/arbiter/knowledge_graph/`)
**Purpose:** Contextual code understanding

**Files:**
- `core.py` - Graph operations
- `knowledge_graph_db.py` - Neo4j integration
- `TECHNICAL_OVERVIEW.md` - Documentation

**Graph Structure:**

**Nodes:**
- CodeFile (path, language, LOC)
- Function (name, complexity, calls)
- Class (name, methods, inheritance)
- Module (name, imports, exports)
- Concept (type, description)
- Bug (id, severity, status)
- Fix (id, success, timestamp)
- Developer (name, email)
- Commit (sha, message, date)

**Relationships:**
- IMPORTS
- CALLS
- INHERITS
- USES
- DEPENDS_ON
- RELATES_TO
- FIXED_BY
- AUTHORED_BY
- SIMILAR_TO

**Queries:**
- Find related code
- Trace dependencies
- Impact analysis
- Root cause analysis
- Find similar bugs
- Expert identification

**Multi-Modal Support:**
- Link code to documentation
- Link code to images (diagrams)
- Link code to videos (tutorials)
- Link code to audio (discussions)

---

#### 3.12 Explainable Reasoner (`/arbiter/explainable_reasoner/`)
**Purpose:** Explain Arbiter decisions in human terms

**Files:**
- `explainer.py` - Main explanation generation
- `utils.py` - Helper functions

**Explanation Types:**

**1. Decision Explanations:**
"I fixed this bug by replacing the vulnerable function with a secure alternative because..."

**2. Confidence Scores:**
"I am 85% confident this fix will work based on 47 similar past cases."

**3. Alternative Approaches:**
"I considered 3 other solutions but chose this one because it has the best performance/safety tradeoff."

**4. Risk Assessment:**
"This change has low risk (15%) of introducing regressions based on impact analysis."

**5. Learning Justification:**
"I learned this pattern from 12 successful fixes in similar codebases."

**Formats:**
- Natural language (English)
- Structured JSON
- Visual diagrams
- Step-by-step breakdowns

---

#### 3.13 Meta-Learning Orchestrator (`/arbiter/meta_learning_orchestrator/`)
**Purpose:** Learn from experience and improve

**Files:**
- `orchestrator.py` - Main coordination
- `config.py` - ML configuration
- `data_store.py` - Training data storage

**Learning Cycle:**
```
Experience → Data Collection → Feature Extraction → Training → 
Validation → Deployment → Monitoring → Feedback → [Repeat]
```

**What It Learns:**

**1. Fix Patterns:**
- Successful fix strategies
- Common bug types
- Code smells → fixes mapping

**2. Performance:**
- Resource usage patterns
- Optimization opportunities
- Bottleneck identification

**3. Security:**
- Vulnerability patterns
- Attack vectors
- Defensive coding

**4. Quality:**
- Best practices
- Code patterns
- Design patterns

**5. Context:**
- Project-specific patterns
- Team preferences
- Domain knowledge

**ML Models Used:**
- Random Forest (classification)
- Neural Networks (pattern recognition)
- Reinforcement Learning (decision making)
- Transfer Learning (knowledge transfer)

**Training Data:**
- Historical fixes
- Code repositories
- Bug databases
- Security advisories
- Documentation

---

#### 3.14 Arbiter Growth System (`/arbiter/arbiter_growth/`)
**Purpose:** Track and manage Arbiter's evolution

**Files:** (13 Python files)
- `arbiter_growth_manager.py` - Growth tracking
- `models.py` - Growth metrics models
- `storage_backends.py` - Persistence
- `plugins.py` - Growth plugins
- `metrics.py` - Performance metrics
- `idempotency.py` - Idempotent operations
- `config_store.py` - Configuration management
- `exceptions.py` - Custom exceptions

**Growth Metrics:**
```python
- tasks_completed: int
- tasks_failed: int
- success_rate: float
- avg_fix_time: timedelta
- knowledge_nodes: int
- policies_learned: int
- bugs_prevented: int
- false_positives: int
- human_interventions: int
```

**Growth Stages:**
1. **Novice:** Learning basic patterns
2. **Intermediate:** Autonomous simple fixes
3. **Advanced:** Complex problem solving
4. **Expert:** Proactive improvements
5. **Master:** Teaching other agents

**Capabilities Tracking:**
- Languages mastered
- Frameworks understood
- Security knowledge
- Performance optimization
- Domain expertise

---

#### 3.15 Plugins System (`/arbiter/plugins/`)
**Purpose:** Extensibility through plugins

**Available Plugins:**

**1. Multi-Modal Plugin (`multimodal/`):**
- Text processing (NLP)
- Image analysis (OCR, diagram parsing)
- Audio transcription
- Video analysis
- PDF parsing

**Providers:**
- OpenAI (GPT-4 Vision)
- Anthropic (Claude 3)
- Google (Gemini)
- Local (Ollama with vision models)

**2. SIEM Plugin:**
- Splunk integration
- QRadar integration
- Azure Sentinel
- Custom log forwarding

**3. LLM Adapter Plugin:**
- OpenAI
- Anthropic
- Google
- xAI (Grok)
- Ollama

---

#### 3.16 Monitoring System (`monitoring.py`)
**Purpose:** Real-time system health tracking

**Components:**
- Health checks (all subsystems)
- Resource monitoring (CPU, memory, disk, network)
- Performance tracking
- Error rate monitoring
- Alert generation

**Metrics:**
- System uptime
- Response times
- Queue depths
- Error rates
- Resource utilization

**Alerts:**
- Threshold-based
- Anomaly detection
- Trend analysis
- Predictive alerts

---

#### 3.17 Message Queue Service (`message_queue_service.py`)
**Purpose:** Internal event bus for Arbiter components

**Features:**
- Topic-based pub/sub
- Priority queues
- Message persistence
- Retry logic
- Dead letter queues

**Topics:**
- bug_detected
- fix_applied
- policy_violation
- knowledge_updated
- agent_competed
- feedback_received

---

#### 3.18 Configuration (`config.py`)
**Purpose:** Centralized configuration management

**Features:**
- Environment variable loading
- .env file support
- JSON config files
- Type validation (Pydantic)
- Secret management
- Hot-reload support

---

### Additional SFE Components

#### 3.19 Agent Orchestration (`/agent_orchestration/`)
**Purpose:** Manage AI and human agents

**Files:**
- `crew_manager.py` - Agent lifecycle management
- `crew_config.yaml` - Agent definitions

**Agent Types:**
- AI agents (LLM-powered)
- Human agents (developers, reviewers)
- Plugin agents (external tools)

**Configuration Example:**
```yaml
version: 10.0.0
id: self_fixing_engineer_crew
agents:
  - id: refactor
    name: Refactor Agent
    agent_type: ai
    compliance_controls:
      - id: AC-6
        status: enforced
  - id: security_scanner
    name: Security Scanner
    agent_type: plugin
```

---

#### 3.20 Test Generation (`/test_generation/`)
**Purpose:** Automated comprehensive test creation

**Files:**
- `orchestrator/pipeline.py` - Test generation pipeline
- `gen_agent/agents.py` - Test generation agents
- `compliance_mapper.py` - Compliance-aware testing

**Test Types Generated:**
- Unit tests
- Integration tests
- End-to-end tests
- Security tests
- Performance tests
- Compliance tests

**Frameworks:**
- pytest (Python)
- jest (JavaScript)
- Go testing

**Features:**
- Property-based testing
- Mutation testing
- Coverage-driven generation
- Edge case discovery

---

#### 3.21 Simulation & Sandbox (`/simulation/`)
**Purpose:** Secure code execution and testing

**Files:**
- `simulation_module.py` - Main simulation engine
- `sandbox.py` - Sandboxing logic
- `parallel.py` - Parallel execution
- `quantum.py` - Quantum computing integration
- `dashboard.py` - Results visualization
- `agent_core.py` - Agent simulation

**Sandbox Features:**

**Security:**
- AppArmor profiles
- Seccomp filtering
- Network isolation
- File system restrictions
- Resource limits (CPU, memory, time)

**Execution:**
- Process isolation
- Container-based (Docker)
- VM-based (optional)
- Timeout enforcement

**Monitoring:**
- System calls
- File access
- Network activity
- Resource usage

**Use Cases:**
- Test execution
- Fix validation
- Security testing
- Performance benchmarking

---

#### 3.22 Guardrails (`/guardrails/`)
**Purpose:** Compliance and policy enforcement

**Files:**
- `compliance_mapper.py` - Maps code to compliance requirements
- `audit_log.py` - Audit logging

**Compliance Frameworks:**
- **SOC2:** Security, availability, confidentiality
- **HIPAA:** Healthcare data protection
- **PCI DSS:** Payment card security
- **GDPR:** Data privacy
- **NIST:** Cybersecurity framework
- **ISO 27001:** Information security

**Enforcement:**
- Pre-commit checks
- Runtime monitoring
- Post-deployment validation
- Continuous compliance scanning

---

#### 3.23 Mesh System (`/mesh/`)
**Purpose:** Distributed event bus and checkpoint management

**Files:**
- `event_bus.py` - Event distribution
- `mesh_adapter.py` - External system adapters
- `mesh_policy.py` - Mesh policies
- `checkpoint/` - Checkpoint management

**Event Bus:**
- Distributed pub/sub
- Event sourcing
- Replay capability
- Guaranteed delivery

**Checkpoints:**
- System state snapshots
- Incremental checkpoints
- Compressed storage
- Blockchain anchoring

---

#### 3.24 Plugins (`/self_fixing_engineer/plugins/`)
**Purpose:** External integrations

**10+ Plugin Types:**

**1. Kafka Plugin (`kafka/`):**
- Message production
- Message consumption
- Stream processing

**2. Slack Plugin (`slack_plugin/`):**
- Notifications
- Interactive commands
- File sharing

**3. PagerDuty Plugin (`pagerduty_plugin/`):**
- Incident creation
- Escalation
- On-call management

**4. SIEM Plugin (`siem_plugin/`):**
- Log forwarding
- Alert integration
- Compliance reporting

**5. AWS SNS Plugin (`sns_plugin/`):**
- Push notifications
- Topic management

**6. Google Pub/Sub Plugin (`pubsub_plugin/`):**
- Message publishing
- Subscriptions

**7. Azure Event Grid Plugin (`azure_eventgrid_plugin/`):**
- Event routing
- Filtering

**8. RabbitMQ Plugin (`rabbitmq_plugin/`):**
- Queue management
- Exchange routing

**9. DLT Backend Plugin (`dlt_backend/`):**
- Blockchain integration
- Smart contract interaction

**10. WASM Runner (`wasm_runner.py`):**
- WebAssembly execution
- Sandboxed runtime
- Cross-language support

**11. gRPC Runner (`grpc_runner.py`):**
- gRPC service hosting
- Protocol buffer support
- High-performance RPC

---

#### 3.25 Reinforcement Learning Environment (`/envs/`)
**Purpose:** RL-based code optimization

**Files:**
- `code_health_env.py` - Gym environment
- `evolution.py` - Genetic algorithms

**CodeHealthEnv:**
```python
class CodeHealthEnv(gym.Env):
    observation_space: Box  # Code metrics
    action_space: Discrete  # Actions to take
    
    def reset() -> observation
    def step(action) -> (observation, reward, done, info)
    def render() -> None
```

**State Space:**
- Lines of code
- Complexity metrics
- Test coverage
- Bug count
- Security issues
- Performance metrics

**Action Space:**
- Refactor code
- Add tests
- Optimize algorithm
- Fix security issue
- Improve documentation
- Update dependencies

**Rewards:**
- Increased coverage: +10
- Reduced complexity: +15
- Fixed bug: +20
- Improved performance: +25
- Security fix: +30
- Introduced bug: -50

**Training:**
- PPO algorithm (Stable-Baselines3)
- Continuous learning
- Transfer learning across projects

---

#### 3.26 Blockchain Integration (`/contracts/`, `/fabric_chaincode/`)
**Purpose:** Immutable audit trails and checkpoints

**Hyperledger Fabric (`checkpoint_chaincode.go`):**
```go
// Smart contract for checkpoint storage
type CheckpointChaincode struct {
    contractapi.Contract
}

func (c *CheckpointChaincode) StoreCheckpoint(
    ctx, id, hash, metadata string
) error {
    // Store checkpoint on blockchain
}

func (c *CheckpointChaincode) GetCheckpoint(
    ctx, id string
) (*Checkpoint, error) {
    // Retrieve checkpoint
}

func (c *CheckpointChaincode) VerifyCheckpoint(
    ctx, id, hash string
) (bool, error) {
    // Verify integrity
}
```

**Ethereum/EVM (`CheckpointContract.sol`):**
```solidity
contract CheckpointContract {
    struct Checkpoint {
        string id;
        bytes32 hash;
        uint256 timestamp;
        string metadata;
    }
    
    mapping(string => Checkpoint) public checkpoints;
    
    function storeCheckpoint(
        string memory id,
        bytes32 hash,
        string memory metadata
    ) public {
        // Store on Ethereum
    }
    
    function verifyCheckpoint(
        string memory id,
        bytes32 hash
    ) public view returns (bool) {
        // Verify integrity
    }
}
```

**Benefits:**
- Immutable history
- Tamper detection
- Provenance tracking
- Compliance evidence
- Distributed trust

---

## End-to-End Workflow

### Complete Journey: README → Production

#### Phase 1: User Input
```
User provides:
  - README.md with requirements
  - OR natural language description
  - OR API request with JSON
```

#### Phase 2: Generator Module Processing

**Step 1: Clarification**
- Input: Raw requirements
- Process: Clarifier Agent refines ambiguities
- Output: Structured requirements JSON
- Time: 5-30 seconds

**Step 2: Code Generation**
- Input: Structured requirements
- Process: Codegen Agent generates source code
- LLM: GPT-4/Claude/Gemini/Grok/Ollama
- Output: Application source files
- Time: 30-90 seconds

**Step 3: Security Critique**
- Input: Generated code
- Process: Critique Agent scans with Bandit/Semgrep/ESLint
- Vulnerabilities: SQL injection, XSS, secrets, etc.
- Fixes: Automatically applied
- Output: Secured code
- Time: 15-45 seconds

**Step 4: Test Generation**
- Input: Secured code
- Process: Testgen Agent creates comprehensive tests
- Coverage Target: 90%+
- Output: Test files (test_*.py, *.test.js)
- Time: 20-60 seconds

**Step 5: Deployment Config**
- Input: Code + tests
- Process: Deploy Agent generates configs
- Output: Dockerfile, K8s manifests, CI/CD
- Time: 10-30 seconds

**Step 6: Documentation**
- Input: All artifacts
- Process: Docgen Agent creates docs
- Output: README, API docs, diagrams
- Time: 15-40 seconds

**Total Generator Time: 1.5-5 minutes**

---

#### Phase 3: OmniCore Engine Coordination

**Step 7: Message Bus Routing**
- Serialize generator outputs
- Publish to sharded message bus
- Route to subscribers
- Encrypt sensitive data
- Audit all events

**Step 8: State Persistence**
- Store in database (PostgreSQL/SQLite)
- Create audit records (Merkle trees)
- Save to S3 (if configured)
- Blockchain checkpoint (optional)

**Step 9: Plugin Invocation**
- Trigger registered plugins
- Pass context and artifacts
- Collect plugin outputs
- Aggregate results

**Step 10: Workflow Tracking**
- Update workflow status
- Emit progress events
- Send notifications (Slack/PagerDuty)
- Update dashboard

**Total OmniCore Time: 10-30 seconds**

---

#### Phase 4: Self-Fixing Engineer Processing

**Step 11: Arbiter Initialization**
- Receive artifacts from OmniCore
- Load configuration and policies
- Initialize knowledge graph
- Prepare sandbox environment

**Step 12: Codebase Analysis**
- Parse all files (AST)
- Extract entities and relationships
- Build knowledge graph
- Calculate code health metrics
- Identify potential issues

**Analysis Metrics:**
- Cyclomatic complexity
- Cognitive complexity
- Maintainability index
- Technical debt
- Security score
- Test coverage
- Documentation coverage

**Step 13: Issue Detection**
- Static analysis (deeper than Critique)
- Runtime analysis (simulation)
- Security scanning
- Performance profiling
- Compliance checking

**Step 14: Auto-Remediation**
- Bug Manager processes issues
- ML model predicts best fix
- Generate fix code
- Validate in sandbox
- Apply if successful
- Learn from outcome

**Step 15: Optimization**
- Performance optimization (RL agent)
- Code refactoring
- Test improvement
- Documentation enhancement
- Dependency updates

**Step 16: Compliance Validation**
- Check against Guardrails
- Validate SOC2/HIPAA/PCI compliance
- Generate compliance reports
- Store audit trail
- Blockchain checkpoint

**Step 17: Simulation & Testing**
- Run all tests in sandbox
- Security testing
- Performance benchmarking
- Load testing
- Chaos engineering (optional)

**Step 18: Knowledge Graph Update**
- Add new nodes (files, functions, concepts)
- Create relationships
- Update embeddings
- Index for search
- Persist to Neo4j

**Step 19: Continuous Monitoring Setup**
- Deploy monitoring agents
- Configure alerts
- Set up dashboards
- Enable self-healing
- Start periodic scans

**Total SFE Time: 2-10 minutes**

---

#### Phase 5: Deployment & Monitoring

**Step 20: Deployment**
- Build Docker image
- Push to registry
- Deploy to Kubernetes
- Apply configurations
- Run smoke tests
- Enable traffic

**Step 21: Observability**
- Prometheus metrics collection
- OpenTelemetry traces
- Structured logs (JSON)
- Grafana dashboards
- Alert rules

**Step 22: Self-Healing Active**
- Arbiter monitors application
- Detects anomalies
- Auto-fixes issues
- Notifies team (if needed)
- Learns from production

**Step 23: Continuous Improvement**
- Meta-learning from usage
- Performance optimization
- Security hardening
- Test enhancement
- Documentation updates

---

### Total Time: README → Production
- **Fast Path:** 4-6 minutes (simple app)
- **Standard Path:** 10-15 minutes (typical app)
- **Complex Path:** 20-45 minutes (enterprise app)

### Ongoing Operations
- **Monitoring:** 24/7 automated
- **Self-Healing:** Automatic (minutes to hours)
- **Updates:** Periodic (daily/weekly)
- **Evolution:** Continuous learning

---

## Integration Architecture

### How Modules Connect

#### Generator ↔ OmniCore
```
Generator                    OmniCore Engine
   ↓                              ↓
Agents → Plugin Wrapper    MessageBus.subscribe()
          ↓                       ↓
      Artifacts            PluginRegistry
          ↓                       ↓
    Publish Event          Database.store()
          ↓                       ↓
      Event Data           Route to SFE
```

**Communication:**
- Events via message bus
- REST API calls (optional)
- Shared database (AgentState)
- File system (output/)

---

#### OmniCore ↔ SFE
```
OmniCore                     Self-Fixing Engineer
   ↓                              ↓
MessageBus               Arbiter.initialize()
   ↓                              ↓
Publish                  Subscribe to topics
  "sfe_workflow"            ↓
   ↓                     Receive artifacts
Route Event                   ↓
   ↓                     Load into KG
Database                      ↓
  AgentState             Analyze & Fix
   ↓                          ↓
Update Status            Publish results
   ↓                          ↓
Checkpoint               Update state
```

**Communication:**
- ShardedMessageBus (primary)
- Database (shared state)
- REST API (health checks)
- WebSocket (real-time updates)

---

#### Generator ↔ SFE (Direct)
```
Generator Test → SFE Test Generation
Generator Code → SFE Codebase Analyzer
Generator Docs → SFE Knowledge Graph
```

**Use Cases:**
- Generator creates initial tests
- SFE enhances with more tests
- SFE validates generator output
- Feedback loop for improvement

---

### Message Flow Example

**Scenario: Bug Detected in Production**

```
1. Application throws exception
   ↓
2. SFE Arbiter catches it (monitoring.py)
   ↓
3. Bug Manager analyzes (bug_manager.py)
   ↓
4. Publishes "arbiter:bug_detected" event
   ↓
5. OmniCore MessageBus routes event
   ↓
6. PluginService.handle_arbiter_bug() receives
   ↓
7. Bug Manager attempts auto-remediation
   ↓
8. If successful: apply fix, log, done
   ↓
9. If failed: notify team (Slack/PagerDuty)
   ↓
10. Human fixes issue
    ↓
11. Meta-Learning stores solution
    ↓
12. Future similar bugs auto-fixed
```

---

## All Features & Functions Catalog

### Complete Feature Matrix

| Category | Feature | Generator | OmniCore | SFE | Status |
|----------|---------|-----------|----------|-----|--------|
| **Code Generation** | | | | | |
| Multi-language support | ✓ | ✓ | | ✓ | Production |
| Framework awareness | ✓ | | | ✓ | Production |
| Design patterns | ✓ | | ✓ | ✓ | Production |
| Dependency management | ✓ | | ✓ | ✓ | Production |
| **Testing** | | | | | |
| Unit test generation | ✓ | | ✓ | ✓ | Production |
| Integration tests | ✓ | | ✓ | ✓ | Production |
| Property-based tests | ✓ | | ✓ | ✓ | Production |
| Coverage analysis | | | ✓ | ✓ | Production |
| **Security** | | | | | |
| Static analysis | ✓ | | ✓ | ✓ | Production |
| Vulnerability scanning | ✓ | | ✓ | ✓ | Production |
| Secret detection | ✓ | ✓ | ✓ | ✓ | Production |
| PII scrubbing | ✓ | ✓ | ✓ | ✓ | Production |
| **Deployment** | | | | | |
| Docker generation | ✓ | | | ✓ | Production |
| Kubernetes manifests | ✓ | | | ✓ | Production |
| Helm charts | ✓ | | | ✓ | Production |
| Terraform configs | ✓ | | | ✓ | Production |
| CI/CD pipelines | ✓ | | | ✓ | Production |
| **Compliance** | | | | | |
| SOC2 | ✓ | ✓ | ✓ | ✓ | Production |
| HIPAA | ✓ | ✓ | ✓ | ✓ | Production |
| PCI DSS | ✓ | ✓ | ✓ | ✓ | Production |
| GDPR | ✓ | ✓ | ✓ | ✓ | Production |
| NIST | | | ✓ | ✓ | Production |
| **Observability** | | | | | |
| Prometheus metrics | ✓ | ✓ | ✓ | ✓ | Production |
| OpenTelemetry | ✓ | ✓ | ✓ | ✓ | Production |
| Structured logging | ✓ | ✓ | ✓ | ✓ | Production |
| Grafana dashboards | | ✓ | ✓ | ✓ | Production |
| **AI/ML** | | | | | |
| LLM integration | ✓ | | ✓ | ✓ | Production |
| Reinforcement learning | | | ✓ | ✓ | Production |
| Meta-learning | | | ✓ | ✓ | Production |
| Multi-modal | | | ✓ | ✓ | Production |
| **Self-Healing** | | | | | |
| Bug detection | | | ✓ | ✓ | Production |
| Auto-remediation | | | ✓ | ✓ | Production |
| Root cause analysis | | | ✓ | ✓ | Production |
| Rollback on failure | | | ✓ | ✓ | Production |
| **Blockchain** | | | | | |
| Hyperledger Fabric | | | ✓ | ✓ | Production |
| Ethereum/EVM | | | ✓ | ✓ | Production |
| Checkpoint storage | | | ✓ | ✓ | Production |
| Tamper detection | | ✓ | ✓ | ✓ | Production |
| **Integration** | | | | | |
| Kafka | | ✓ | ✓ | ✓ | Production |
| Redis | | ✓ | ✓ | ✓ | Production |
| PostgreSQL | | ✓ | ✓ | ✓ | Production |
| Neo4j | | | ✓ | ✓ | Production |
| Slack | | | ✓ | ✓ | Production |
| PagerDuty | | | ✓ | ✓ | Production |
| SIEM | | | ✓ | ✓ | Production |

---

## File Structure Reference

### Complete Directory Tree

```
The_Code_Factory_Working_V2/
│
├── generator/ (171 Python files)
│   ├── agents/
│   │   ├── codegen_agent/
│   │   │   ├── codegen_agent.py
│   │   │   ├── codegen_prompt.py
│   │   │   └── codegen_response_handler.py
│   │   ├── critique_agent/
│   │   │   ├── critique_agent.py
│   │   │   ├── critique_linter.py
│   │   │   ├── critique_fixer.py
│   │   │   └── critique_prompt.py
│   │   ├── testgen_agent/
│   │   │   ├── testgen_agent.py
│   │   │   ├── testgen_prompt.py
│   │   │   └── testgen_validator.py
│   │   ├── deploy_agent/
│   │   │   ├── deploy_agent.py
│   │   │   ├── deploy_prompt.py
│   │   │   ├── deploy_validator.py
│   │   │   └── deploy_response_handler.py
│   │   ├── docgen_agent/
│   │   │   ├── docgen_agent.py
│   │   │   ├── docgen_prompt.py
│   │   │   └── docgen_response_validator.py
│   │   └── generator_plugin_wrapper.py
│   ├── clarifier/
│   ├── audit_log/
│   ├── intent_parser/
│   ├── runner/
│   │   └── providers/
│   ├── main/
│   │   ├── main.py
│   │   ├── cli.py
│   │   ├── api.py
│   │   └── gui.py
│   ├── config.yaml
│   └── requirements.txt
│
├── omnicore_engine/ (77 Python files)
│   ├── core.py
│   ├── engines.py
│   ├── cli.py
│   ├── fastapi_app.py
│   ├── plugin_registry.py
│   ├── metrics.py
│   ├── audit.py
│   ├── meta_supervisor.py
│   ├── security_config.py
│   ├── database/
│   │   ├── database.py
│   │   └── models.py
│   ├── message_bus/
│   │   ├── sharded_message_bus.py
│   │   ├── message_types.py
│   │   ├── hash_ring.py
│   │   ├── backpressure.py
│   │   ├── rate_limit.py
│   │   ├── encryption.py
│   │   ├── cache.py
│   │   ├── dead_letter_queue.py
│   │   ├── guardian.py
│   │   ├── resilience.py
│   │   └── integrations/
│   │       └── kafka_sink_adapter.py
│   └── docs/
│       └── ARCHITECTURE.md
│
├── self_fixing_engineer/ (552 Python files)
│   ├── arbiter/ (26,626+ lines)
│   │   ├── arbiter.py (3,032 lines)
│   │   ├── agent_state.py
│   │   ├── arbiter_constitution.py
│   │   ├── arena.py
│   │   ├── config.py
│   │   ├── knowledge_loader.py
│   │   ├── codebase_analyzer.py
│   │   ├── feedback.py
│   │   ├── human_loop.py
│   │   ├── monitoring.py
│   │   ├── message_queue_service.py
│   │   ├── metrics.py
│   │   ├── utils.py
│   │   ├── arbiter_growth/
│   │   │   ├── arbiter_growth_manager.py
│   │   │   ├── models.py
│   │   │   ├── storage_backends.py
│   │   │   ├── plugins.py
│   │   │   ├── metrics.py
│   │   │   ├── idempotency.py
│   │   │   └── config_store.py
│   │   ├── bug_manager/
│   │   │   ├── bug_manager.py (1,200+ lines)
│   │   │   ├── remediations.py
│   │   │   ├── notifications.py (750+ lines)
│   │   │   └── utils.py
│   │   ├── knowledge_graph/
│   │   │   ├── core.py
│   │   │   └── knowledge_graph_db.py
│   │   ├── explainable_reasoner/
│   │   │   ├── explainer.py
│   │   │   └── utils.py
│   │   ├── meta_learning_orchestrator/
│   │   │   ├── orchestrator.py
│   │   │   ├── config.py
│   │   │   └── data_store.py
│   │   ├── learner/
│   │   ├── policy/
│   │   │   ├── core.py
│   │   │   └── policies.json
│   │   ├── plugins/
│   │   │   └── multimodal/
│   │   └── models/
│   │       ├── postgres_client.py
│   │       ├── redis_client.py
│   │       ├── knowledge_graph_db.py
│   │       ├── audit_ledger_client.py
│   │       ├── merkle_tree.py
│   │       ├── feature_store_client.py
│   │       └── meta_learning_data_store.py
│   ├── agent_orchestration/
│   │   ├── crew_manager.py
│   │   └── crew_config.yaml
│   ├── test_generation/
│   │   ├── orchestrator/
│   │   │   └── pipeline.py
│   │   ├── gen_agent/
│   │   │   └── agents.py
│   │   ├── compliance_mapper.py
│   │   └── backends.py
│   ├── simulation/
│   │   ├── simulation_module.py
│   │   ├── sandbox.py
│   │   ├── parallel.py
│   │   ├── quantum.py
│   │   ├── dashboard.py
│   │   └── agent_core.py
│   ├── guardrails/
│   │   ├── compliance_mapper.py
│   │   └── audit_log.py
│   ├── mesh/
│   │   ├── event_bus.py
│   │   ├── mesh_adapter.py
│   │   ├── mesh_policy.py
│   │   └── checkpoint/
│   ├── envs/
│   │   ├── code_health_env.py
│   │   └── evolution.py
│   ├── plugins/
│   │   ├── kafka/
│   │   ├── slack_plugin/
│   │   ├── pagerduty_plugin/
│   │   ├── siem_plugin/
│   │   ├── sns_plugin/
│   │   ├── pubsub_plugin/
│   │   ├── azure_eventgrid_plugin/
│   │   ├── rabbitmq_plugin/
│   │   ├── dlt_backend/
│   │   ├── wasm_runner.py
│   │   └── grpc_runner.py
│   ├── intent_capture/
│   ├── refactor_agent/
│   ├── self_healing_import_fixer/
│   ├── contracts/
│   │   └── CheckpointContract.sol
│   ├── fabric_chaincode/
│   │   └── checkpoint_chaincode.go
│   └── proto/
│
├── monitoring/
│   ├── prometheus/
│   └── grafana/
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── cd.yml
│       ├── security.yml
│       └── dependency-updates.yml
│
├── requirements.txt (374 dependencies)
├── pyproject.toml
├── Makefile
├── docker-compose.yml
├── Dockerfile
├── README.md
└── REPOSITORY_CAPABILITIES.md (this file)
```

---

## Technology Stack

### Programming Languages
- **Python 3.11+** (primary, 803 files)
- **Go** (Hyperledger Fabric chaincode)
- **Solidity** (Ethereum smart contracts)
- **JavaScript/TypeScript** (generated code)
- **Shell/Bash** (scripts)
- **YAML** (configuration)
- **JSON** (configuration, data)

### AI & Machine Learning
- **OpenAI** (GPT-4, GPT-3.5)
- **Anthropic** (Claude 3)
- **Google** (Gemini)
- **xAI** (Grok)
- **Ollama** (Local LLMs)
- **Stable-Baselines3** (RL - PPO)
- **Gymnasium** (RL environments)
- **scikit-learn** (ML utilities)
- **TensorFlow/PyTorch** (optional)

### Databases & Storage
- **PostgreSQL** (primary database)
- **SQLite** (development)
- **Redis** (caching, pub/sub)
- **Neo4j** (knowledge graphs)
- **S3-compatible** (object storage)
- **File system** (local storage)

### Message Queues & Events
- **Custom ShardedMessageBus** (primary)
- **Kafka** (enterprise messaging)
- **RabbitMQ** (AMQP)
- **Redis Pub/Sub** (lightweight)
- **AWS SNS/SQS** (cloud)
- **Google Pub/Sub** (cloud)
- **Azure Event Grid** (cloud)

### Blockchain & DLT
- **Hyperledger Fabric** (permissioned blockchain)
- **Ethereum** (public blockchain)
- **Solidity** (smart contracts)
- **Web3.py** (Ethereum interaction)

### Web Frameworks & APIs
- **FastAPI** (REST APIs)
- **Uvicorn** (ASGI server)
- **aiohttp** (async HTTP client)
- **httpx** (modern HTTP client)
- **WebSocket** (real-time)

### Security & Compliance
- **Bandit** (Python security)
- **Semgrep** (multi-language SAST)
- **ESLint** (JavaScript linting)
- **Trivy** (container scanning)
- **Presidio** (PII detection)
- **Cryptography** (encryption)
- **PyJWT** (JWT tokens)

### Observability & Monitoring
- **Prometheus** (metrics)
- **Grafana** (dashboards)
- **OpenTelemetry** (tracing)
- **structlog** (structured logging)
- **Sentry** (error tracking)

### Testing
- **pytest** (Python testing)
- **pytest-asyncio** (async tests)
- **pytest-cov** (coverage)
- **hypothesis** (property-based)
- **jest** (JavaScript testing)

### DevOps & Infrastructure
- **Docker** (containerization)
- **Kubernetes** (orchestration)
- **Helm** (K8s package manager)
- **Terraform** (IaC)
- **GitHub Actions** (CI/CD)
- **Make** (build automation)

### Code Quality
- **Black** (Python formatting)
- **Ruff** (Python linting)
- **Flake8** (Python style)
- **Pylint** (Python linting)
- **mypy** (type checking)
- **pre-commit** (Git hooks)

### Dependencies (374 total)
Key packages:
- `aiofiles`, `aiohttp`, `aiokafka`, `aiolimiter`
- `asyncpg`, `aiosqlite`, `redis`, `sqlalchemy`
- `fastapi`, `pydantic`, `pydantic-settings`
- `prometheus_client`, `opentelemetry-*`
- `cryptography`, `python-jose`, `python-dotenv`
- `gymnasium`, `stable-baselines3`
- `numpy`, `pandas`, `scipy`, `networkx`
- `boto3`, `google-cloud-*`, `azure-*`
- And 300+ more...

---

## Configuration & Environment

### Environment Variables

**Core:**
```bash
APP_ENV=production|development
DEBUG=true|false
LOG_LEVEL=DEBUG|INFO|WARNING|ERROR|CRITICAL
```

**Database:**
```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host/db
REDIS_URL=redis://localhost:6379
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

**Security:**
```bash
SECRET_KEY=your-secret-key-here
JWT_SECRET_KEY=your-jwt-secret
ENCRYPTION_KEY=base64-encoded-32-byte-key
```

**AI/LLM:**
```bash
GROK_API_KEY=your-grok-key
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=your-gemini-key
OLLAMA_API_URL=http://localhost:11434
LLM_ADAPTER=openai|anthropic|google|xai|ollama
LLM_MODEL=gpt-4|claude-3|gemini-pro|grok-beta|llama3
```

**Observability:**
```bash
PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus
PROMETHEUS_GATEWAY=http://localhost:9091
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
SENTRY_DSN=https://...@sentry.io/...
```

**Integrations:**
```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SLACK_AUTH_TOKEN=xoxb-...
PAGERDUTY_ROUTING_KEY=your-routing-key
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
AWS_ACCESS_KEY_ID=your-aws-key
AWS_SECRET_ACCESS_KEY=your-aws-secret
GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
```

**Arbiter:**
```bash
ARBITER_URL=http://localhost:8000
ARENA_PORT=8080
CODEBASE_PATHS=/path/to/project1,/path/to/project2
ENABLE_CRITICAL_FAILURES=false
AI_API_TIMEOUT=30
MEMORY_LIMIT=40
PERIODIC_SCAN_INTERVAL_S=3600
RL_MODEL_PATH=./models/ppo_model.zip
```

**Blockchain:**
```bash
FABRIC_NETWORK_URL=localhost:7051
FABRIC_CHANNEL_NAME=mychannel
ETHEREUM_RPC_URL=https://mainnet.infura.io/v3/...
ETHEREUM_PRIVATE_KEY=0x...
CHECKPOINT_BACKEND_TYPE=fabric|ethereum|filesystem
```

**Email:**
```bash
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USERNAME=your-email@gmail.com
EMAIL_SMTP_PASSWORD=your-password
EMAIL_SENDER=noreply@codefactory.com
EMAIL_USE_TLS=true
```

### Configuration Files

**generator/config.yaml:**
```yaml
llm_providers:
  default: openai
  fallback: [anthropic, google]
  timeout: 30
  retry: 3

agents:
  codegen:
    model: gpt-4
    temperature: 0.2
  testgen:
    model: gpt-4
    temperature: 0.1
    coverage_target: 90

security:
  enable_scanning: true
  tools: [bandit, semgrep, eslint]
```

**omnicore_engine/config.yaml:**
```yaml
message_bus:
  shards: 4
  redis_url: ${REDIS_URL}
  encryption: true
  
database:
  url: ${DATABASE_URL}
  pool_size: 10
  echo: false

plugins:
  autoload: true
  directories: [./plugins, ../self_fixing_engineer/plugins]
```

**self_fixing_engineer/arbiter/arbiter_config.json:**
```json
{
  "arbiter": {
    "mode": "live",
    "version": "1.0.0",
    "policies": "./policies.json",
    "constitution": "./arbiter_constitution.py"
  },
  "arena": {
    "max_agents": 10,
    "timeout": 300,
    "rounds": 5
  },
  "learning": {
    "rl_enabled": true,
    "meta_learning_enabled": true,
    "model_save_interval": 100
  }
}
```

---

## Summary

The Code Factory Platform is a **comprehensive, production-ready ecosystem** that:

✅ **Generates** complete applications from READMEs (171 files)  
✅ **Orchestrates** all components through intelligent routing (77 files)  
✅ **Maintains** code automatically with AI-powered self-healing (552 files)  
✅ **Learns** continuously from every operation  
✅ **Complies** with enterprise security and regulatory standards  
✅ **Monitors** everything with comprehensive observability  
✅ **Evolves** through reinforcement learning and genetic algorithms  
✅ **Audits** immutably with blockchain integration  

**Total Platform Stats:**
- 803 Python files
- 26,626+ lines in Arbiter alone
- 374 production dependencies
- 20+ major capability categories
- 100+ individual features
- 10+ external integrations
- 5+ compliance frameworks
- 3 tightly integrated modules

The **Arbiter AI** is the central intelligence that:
- Coordinates 552 SFE files
- Manages self-healing operations
- Enforces policies and compliance
- Builds knowledge graphs
- Runs agent competitions
- Provides human oversight
- Learns and improves continuously

**From README to Production in minutes. Self-healing for life.**

---

*© 2025 Novatrax Labs LLC - Proprietary Technology*  
*Crafted in Fairhope, Alabama, USA 🇺🇸*

---

**End of Document**

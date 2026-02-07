<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Generator Workflow and Server Module Communication Analysis

## Executive Summary

This document provides a deep analysis of how the Generator module workflow operates and communicates with the Server module in The Code Factory Platform. The architecture follows a centralized coordination pattern through the **OmniCore Engine**, which acts as the message bus and routing layer between all components.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Key Components](#key-components)
3. [Communication Flow](#communication-flow)
4. [Generator Module Workflow](#generator-module-workflow)
5. [Server Module Integration](#server-module-integration)
6. [Message Bus Architecture](#message-bus-architecture)
7. [Agent Pipeline Details](#agent-pipeline-details)
8. [API Endpoints and Triggers](#api-endpoints-and-triggers)
9. [Data Flow Diagrams](#data-flow-diagrams)

---

## Architecture Overview

The Code Factory Platform uses a **three-tier architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                            │
│   (Web UI, CLI, API Clients)                                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SERVER MODULE                              │
│   FastAPI Application (server/main.py)                         │
│   ├── Routers (jobs, generator, omnicore, sfe)                 │
│   ├── Services (GeneratorService, OmniCoreService)             │
│   └── Schemas & Storage                                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OMNICORE ENGINE                              │
│   Central Coordination Layer                                    │
│   ├── Message Bus (Sharded, Priority-based)                    │
│   ├── Plugin Registry                                          │
│   └── Job Routing & Orchestration                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GENERATOR MODULE                             │
│   Code Generation Pipeline                                      │
│   ├── Clarifier (LLM/Rule-based)                               │
│   ├── Codegen Agent                                            │
│   ├── Testgen Agent                                            │
│   ├── Deploy Agent                                             │
│   ├── Docgen Agent                                             │
│   └── Critique Agent                                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Components

### 1. Server Module (`server/`)

| Component | File | Purpose |
|-----------|------|---------|
| Main Application | `server/main.py` | FastAPI application entry point |
| Generator Router | `server/routers/generator.py` | Handles file uploads and generation triggers |
| Jobs Router | `server/routers/jobs.py` | Job lifecycle management |
| OmniCore Router | `server/routers/omnicore.py` | Direct OmniCore interactions |
| GeneratorService | `server/services/generator_service.py` | Service layer for generator operations |
| OmniCoreService | `server/services/omnicore_service.py` | Service layer for OmniCore coordination |

### 2. Generator Module (`generator/`)

| Component | Directory | Purpose |
|-----------|-----------|---------|
| Main Entry | `generator/main/` | CLI, GUI, and API interfaces |
| Agents | `generator/agents/` | AI-powered code generation agents |
| Clarifier | `generator/clarifier/` | Requirement clarification and analysis |
| Runner | `generator/runner/` | LLM client, metrics, and logging |
| Intent Parser | `generator/intent_parser/` | Natural language requirement parsing |

### 3. OmniCore Engine (`omnicore_engine/`)

| Component | File/Directory | Purpose |
|-----------|----------------|---------|
| Core | `omnicore_engine/core.py` | Core utilities and serialization |
| Message Bus | `omnicore_engine/message_bus/` | Sharded message bus implementation |
| Plugin Registry | `omnicore_engine/plugin_registry.py` | Plugin management |
| Audit | `omnicore_engine/audit.py` | Audit logging |

---

## Communication Flow

### High-Level Communication Pattern

```
                        HTTP Request
                             │
                             ▼
┌─────────────────────────────────────────────────┐
│              Server Module                       │
│  ┌──────────────────────────────────────────┐  │
│  │    Router (generator.py, jobs.py)        │  │
│  └──────────────────┬───────────────────────┘  │
│                     │                           │
│                     ▼                           │
│  ┌──────────────────────────────────────────┐  │
│  │  GeneratorService / OmniCoreService      │  │
│  └──────────────────┬───────────────────────┘  │
└─────────────────────┼───────────────────────────┘
                      │
                      ▼ route_job()
┌─────────────────────────────────────────────────┐
│           OmniCore Engine                        │
│  ┌──────────────────────────────────────────┐  │
│  │       Message Bus (Sharded)              │  │
│  │  • Priority queuing                      │  │
│  │  • Circuit breakers                      │  │
│  │  • Dead letter queue                     │  │
│  └──────────────────┬───────────────────────┘  │
└─────────────────────┼───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           Generator Module                       │
│  ┌──────────────────────────────────────────┐  │
│  │          Agent Pipeline                   │  │
│  │  Clarifier → Codegen → Testgen →         │  │
│  │  Deploy → Docgen → Critique              │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

---

## Generator Module Workflow

### Pipeline Stages

The generator follows a sequential pipeline with the following stages:

```python
# From server/schemas/jobs.py - Job Stages
class JobStage(str, Enum):
    UPLOAD = "upload"                                    # Stage 1: File upload
    GENERATOR_CLARIFICATION = "generator_clarification"  # Stage 2: Requirement clarification
    GENERATOR_GENERATION = "generator_generation"        # Stage 3: Code generation
    OMNICORE_PROCESSING = "omnicore_processing"         # Stage 4: OmniCore coordination
    SFE_ANALYSIS = "sfe_analysis"                       # Stage 5: Self-Fixing Engineer analysis
    SFE_FIXING = "sfe_fixing"                           # Stage 6: Automated fixes
    COMPLETED = "completed"                              # Final stage
```

### Detailed Workflow Steps

#### Step 1: Job Creation
```
POST /api/jobs/
  └── Creates job entry in jobs_db
  └── Sets status: PENDING, stage: UPLOAD
```

#### Step 2: File Upload
```
POST /api/generator/{job_id}/upload
  └── Validates file (README.md)
  └── Stores file content
  └── Triggers background pipeline via _trigger_pipeline_background()
```

#### Step 3: Clarification Stage
```python
# From server/routers/generator.py
async def _trigger_pipeline_background(job_id, readme_content, generator_service):
    # Auto-detect language from README
    language = detect_language_from_content(readme_content)
    
    # Run clarification
    clarify_result = await generator_service.clarify_requirements(
        job_id=job_id,
        readme_content=readme_content,
    )
    
    # Store questions for user response if needed
    if clarify_result.get("questions_count", 0) > 0:
        job.metadata["clarification_questions"] = clarify_result["clarifications"]
```

#### Step 4: Code Generation Pipeline
```python
# Full pipeline execution
result = await generator_service.run_full_pipeline(
    job_id=job_id,
    readme_content=readme_content,
    language=language,
    include_tests=True,
    include_deployment=True,
    include_docs=True,
    run_critique=True,
)
```

---

## Server Module Integration

### GeneratorService Communication

The `GeneratorService` (in `server/services/generator_service.py`) acts as the bridge between the server and generator:

```python
class GeneratorService:
    def __init__(self, storage_path=None, omnicore_service=None):
        self.storage_path = storage_path or Path("./uploads")
        self.omnicore_service = omnicore_service
    
    async def create_generation_job(self, job_id, files, metadata):
        """Routes job creation through OmniCore"""
        if self.omnicore_service:
            payload = {
                "action": "create_job",
                "job_id": job_id,
                "files": files,
                "metadata": metadata,
            }
            result = await self.omnicore_service.route_job(
                job_id=job_id,
                source_module="api",
                target_module="generator",
                payload=payload,
            )
            return result
```

### OmniCoreService Coordination

The `OmniCoreService` (in `server/services/omnicore_service.py`) manages:

1. **Agent Loading**: Lazy-loads generator agents on startup
2. **LLM Configuration**: Validates and configures LLM providers
3. **Job Routing**: Routes jobs to appropriate agents
4. **Message Bus Integration**: Connects to OmniCore message bus

```python
class OmniCoreService:
    def __init__(self):
        # Track agent availability
        self.agents_available = {
            "codegen": False,
            "testgen": False,
            "deploy": False,
            "docgen": False,
            "critique": False,
            "clarifier": False,
        }
        
        # Load agents from generator module
        self._load_agents()
        
        # Initialize OmniCore components
        self._init_omnicore_components()
    
    def _load_agents(self):
        """Loads each agent module and marks availability"""
        try:
            from generator.agents.codegen_agent.codegen_agent import generate_code
            self._codegen_func = generate_code
            self.agents_available["codegen"] = True
        except ImportError as e:
            logger.warning(f"Codegen agent unavailable: {e}")
```

---

## Message Bus Architecture

### OmniCore Sharded Message Bus (OMSB)

The message bus (`omnicore_engine/message_bus/`) provides:

| Feature | Component | Purpose |
|---------|-----------|---------|
| Sharding | `ShardedMessageBus` | Distributes load across shards |
| Prioritization | `Message` | Priority-based message handling |
| Resilience | `CircuitBreaker`, `RetryPolicy` | Fault tolerance |
| Dead Letter | `DeadLetterQueue` | Failed message handling |
| Rate Limiting | `RateLimiter` | Prevent overload |
| Encryption | `FernetEncryption` | Secure message content |
| Caching | `MessageCache` | Response caching |

### Message Flow

```
Producer (Server) 
    │
    ▼
┌─────────────────────────────────┐
│     ShardedMessageBus           │
│  ┌───────────────────────────┐  │
│  │   RateLimiter (check)     │  │
│  └─────────────┬─────────────┘  │
│                ▼                 │
│  ┌───────────────────────────┐  │
│  │  CircuitBreaker (check)   │  │
│  └─────────────┬─────────────┘  │
│                ▼                 │
│  ┌───────────────────────────┐  │
│  │  ConsistentHashRing       │  │
│  │  (shard selection)        │  │
│  └─────────────┬─────────────┘  │
│                ▼                 │
│  ┌───────────────────────────┐  │
│  │  Message Queue (priority) │  │
│  └─────────────┬─────────────┘  │
└────────────────┼────────────────┘
                 ▼
Consumer (Generator Agents)
```

---

## Agent Pipeline Details

### Codegen Agent (`generator/agents/codegen_agent/`)

**Entry Point**: `codegen_agent.py::generate_code()`

**Dependencies**:
- `generator.runner.llm_client` - LLM API calls
- `generator.runner.runner_logging` - Audit logging
- `generator.runner.runner_metrics` - Prometheus metrics
- `generator.runner.runner_security_utils` - Security scanning

**Flow**:
```
Input (README content)
    │
    ▼
build_code_generation_prompt()    # Constructs LLM prompt
    │
    ▼
call_llm_api() / call_ensemble_api()  # Calls configured LLM
    │
    ▼
parse_llm_response()              # Extracts code from response
    │
    ▼
add_traceability_comments()       # Adds audit comments
    │
    ▼
scan_for_vulnerabilities()        # Security scan
    │
    ▼
Output (Generated code files)
```

### Clarifier (`generator/clarifier/`)

**Components**:
- `clarifier.py` - Main clarification logic (rule-based)
- `clarifier_llm.py` - LLM-based clarification (GrokLLM)
- `clarifier_prioritizer.py` - Question prioritization
- `clarifier_prompt.py` - Prompt templates

**Flow**:
```
README Content
    │
    ▼
Parse Requirements
    │
    ├── LLM Available? ─Yes─► clarifier_llm.py (GrokLLM)
    │                            │
    │                            ▼
    │                    Generate clarifying questions
    │
    └── No ──────────► clarifier.py (Rule-based)
                            │
                            ▼
                    Pattern-based question generation
    │
    ▼
clarifier_prioritizer.py
    │
    ▼
Prioritized Questions
```

---

## API Endpoints and Triggers

### Job Lifecycle Endpoints

| Endpoint | Method | Purpose | Triggers |
|----------|--------|---------|----------|
| `/api/jobs/` | POST | Create job | Creates job entry |
| `/api/jobs/{id}` | GET | Get job details | Returns job state |
| `/api/jobs/{id}/progress` | GET | Get job progress | Returns stage progress |

### Generator Endpoints

| Endpoint | Method | Purpose | Triggers |
|----------|--------|---------|----------|
| `/api/generator/{id}/upload` | POST | Upload README | Triggers pipeline |
| `/api/generator/{id}/clarification/respond` | POST | Answer questions | Continues pipeline |
| `/api/generator/codegen` | POST | Direct codegen | Runs codegen agent |
| `/api/generator/testgen` | POST | Generate tests | Runs testgen agent |
| `/api/generator/deploy` | POST | Generate deployment | Runs deploy agent |
| `/api/generator/docgen` | POST | Generate docs | Runs docgen agent |
| `/api/generator/critique` | POST | Code review | Runs critique agent |

### OmniCore Endpoints

| Endpoint | Method | Purpose | Triggers |
|----------|--------|---------|----------|
| `/api/omnicore/route` | POST | Route job | Routes through message bus |
| `/api/omnicore/status` | GET | Get status | Returns system status |
| `/api/omnicore/agents` | GET | List agents | Returns agent availability |

---

## Data Flow Diagrams

### Complete Job Workflow

```
┌──────────┐    POST /api/jobs/     ┌──────────────┐
│  Client  │ ─────────────────────► │ Jobs Router  │
└──────────┘                        └──────┬───────┘
                                           │
                                           ▼ Create job
                                    ┌──────────────┐
                                    │   jobs_db    │
                                    └──────────────┘
     │
     │ POST /api/generator/{id}/upload
     ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Generator Router                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  _trigger_pipeline_background()                              │ │
│  │    │                                                         │ │
│  │    ├─► detect_language_from_content()                        │ │
│  │    │                                                         │ │
│  │    ├─► generator_service.clarify_requirements()              │ │
│  │    │         │                                               │ │
│  │    │         └─► omnicore_service.route_job()                │ │
│  │    │                    │                                    │ │
│  │    │                    └─► generator.clarifier              │ │
│  │    │                                                         │ │
│  │    └─► generator_service.run_full_pipeline()                 │ │
│  │              │                                               │ │
│  │              └─► omnicore_service.route_job()                │ │
│  │                       │                                      │ │
│  │                       ├─► codegen_agent                      │ │
│  │                       ├─► testgen_agent                      │ │
│  │                       ├─► deploy_agent                       │ │
│  │                       ├─► docgen_agent                       │ │
│  │                       └─► critique_agent                     │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Agent Communication Pattern

```
                 ┌─────────────────────────────────────┐
                 │         OmniCoreService             │
                 │                                     │
                 │  route_job(job_id, source, target,  │
                 │            payload)                 │
                 │         │                           │
                 │         ▼                           │
                 │  ┌─────────────────────────────┐   │
                 │  │  Match target_module        │   │
                 │  │                             │   │
                 │  │  "generator" ───────────┐   │   │
                 │  │                         │   │   │
                 │  └─────────────────────────┼───┘   │
                 │                            │       │
                 │                            ▼       │
                 │  ┌─────────────────────────────┐   │
                 │  │  Dispatch by action:        │   │
                 │  │                             │   │
                 │  │  "create_job"               │   │
                 │  │  "run_codegen"              │   │
                 │  │  "run_testgen"              │   │
                 │  │  "run_deploy"               │   │
                 │  │  "run_docgen"               │   │
                 │  │  "run_critique"             │   │
                 │  │  "clarify_requirements"     │   │
                 │  └─────────────────────────────┘   │
                 │                                     │
                 └─────────────────────────────────────┘
                                   │
                                   ▼
                 ┌─────────────────────────────────────┐
                 │        Generator Agents             │
                 │                                     │
                 │  ┌──────────┐  ┌──────────┐        │
                 │  │ codegen  │  │ testgen  │        │
                 │  └──────────┘  └──────────┘        │
                 │                                     │
                 │  ┌──────────┐  ┌──────────┐        │
                 │  │ deploy   │  │ docgen   │        │
                 │  └──────────┘  └──────────┘        │
                 │                                     │
                 │  ┌──────────┐  ┌───────────────┐   │
                 │  │ critique │  │ clarifier_llm │   │
                 │  └──────────┘  └───────────────┘   │
                 └─────────────────────────────────────┘
```

---

## Configuration and Environment

### LLM Provider Configuration

The system supports multiple LLM providers:

| Provider | Environment Variable | Default Model |
|----------|---------------------|---------------|
| OpenAI | `OPENAI_API_KEY` | gpt-4 |
| Anthropic | `ANTHROPIC_API_KEY` | claude-3-opus |
| xAI/Grok | `XAI_API_KEY` or `GROK_API_KEY` | grok-1 |
| Google | `GOOGLE_API_KEY` | gemini-pro |
| Ollama | `OLLAMA_HOST` | (local) |

### Auto-Detection Flow

```python
def detect_available_llm_provider():
    """Auto-detects first available LLM provider from environment"""
    providers = [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("grok", "XAI_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
    ]
    for provider, env_var in providers:
        if os.environ.get(env_var):
            return provider
    return None
```

---

## Summary

The Generator module workflow follows these key patterns:

1. **Centralized Routing**: All inter-module communication goes through OmniCore's message bus
2. **Service Abstraction**: `GeneratorService` and `OmniCoreService` provide clean interfaces
3. **Lazy Agent Loading**: Agents are loaded on demand to optimize startup time
4. **Pipeline Architecture**: Jobs flow through sequential stages with proper state tracking
5. **Graceful Degradation**: System continues operating when some agents are unavailable
6. **LLM Provider Flexibility**: Supports multiple LLM providers with auto-detection

The architecture enables:
- **Scalability**: Sharded message bus distributes load
- **Resilience**: Circuit breakers and retry policies handle failures
- **Extensibility**: Plugin system allows adding new agents
- **Observability**: Comprehensive metrics, logging, and tracing

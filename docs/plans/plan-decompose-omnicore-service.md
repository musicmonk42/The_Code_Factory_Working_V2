# Plan: Decompose omnicore_service.py (11,021 lines → 14 focused modules, each <= 250 lines)

## Open Questions

- **`_generate_clarification_questions` (3,763 lines)**: This single method is larger than most files in the codebase. It likely contains deeply interleaved LLM prompt construction, response parsing, and session management. The plan targets extracting it, but the internal decomposition will depend on the actual control flow discovered during implementation. Flagged as the highest-risk extraction.

## Phase 1: Extract ServiceContext and module-level helpers

Decouple shared state from the god-class. Extract 16 module-level helper functions into focused utility modules, and create `ServiceContext` to hold shared runtime state.

### Affected Files

- `server/tests/test_service_context.py` — verify context initialization and access
- `server/services/service_context.py` — shared runtime state dataclass (new, ~120 lines)
- `server/services/helpers/validation.py` — report/structure validators (new, ~120 lines)
- `server/services/helpers/project_detection.py` — language detection, test file checks (new, ~80 lines)
- `server/services/helpers/fallback_generators.py` — README, frontend, critique fallbacks (new, ~250 lines)
- `server/services/helpers/file_utils.py` — path resolution, nesting fixes, imports (new, ~120 lines)
- `server/services/helpers/__init__.py` — re-export all helpers (new)
- `server/services/omnicore_service.py` — import helpers, delegate init to context

### Changes

**`server/services/service_context.py`** (new, ~120 lines):
```python
@dataclass
class ServiceContext:
    """Shared runtime state for all decomposed services."""
    llm_config: dict
    agents: dict
    message_bus: Optional[Any]
    omnicore_engine: Optional[Any]
    omnicore_components_available: dict
    job_output_base: Path
    kafka_producer: Optional[Any]

async def create_service_context(llm_config: dict = None) -> ServiceContext:
    """Initialize shared context once at startup."""
```

**Helper modules** — split the 16 module-level functions by concern:

| Module | Functions | Est. Lines |
|--------|-----------|------------|
| `helpers/validation.py` | `_validate_report_structure`, `_validate_helm_chart_structure`, `_create_placeholder_critique_report` | ~120 |
| `helpers/project_detection.py` | `_detect_project_language`, `_is_test_file`, `_extract_project_name_from_path_or_payload`, `_is_third_party_import_error` | ~80 |
| `helpers/fallback_generators.py` | `_generate_fallback_readme`, `_generate_fallback_frontend_files`, `_load_readme_from_disk` | ~250 |
| `helpers/file_utils.py` | `_ensure_python_package_structure`, `_pre_materialization_import_check`, `_fix_double_nesting`, `_build_delta_prompt`, `_load_sfe_analysis_report`, `_invalidate_sfe_analysis_cache` | ~120 |

**`server/services/omnicore_service.py`**:
- Replace 16 inline functions with imports from `helpers/`
- Replace `__init__` body: accept `ServiceContext` as constructor parameter
- Store `self._ctx`

### Unit Tests

- `server/tests/test_service_context.py`:
  - `test_context_creation_with_defaults` — verify context initializes with safe defaults
  - `test_context_agents_lazy_loaded` — verify agents dict starts empty
  - `test_context_components_graceful_degradation` — verify missing components = False flags

### CI Validation

```bash
pytest server/tests/test_service_context.py -v
```

---

## Phase 2: Extract domain services — small domains first

Extract the 5 smaller domain groups (admin, audit, diagnostics, message bus, SFE dispatch) into their own service modules. These are clean extractions with well-bounded method sets.

### Affected Files

- `server/tests/test_admin_service.py` — verify admin ops extraction
- `server/tests/test_message_bus_service.py` — verify bus ops extraction
- `server/services/admin_service.py` — plugins, DB, circuit breakers, rate limits (new, ~250 lines)
- `server/services/audit_query_service.py` — audit trail queries, log reading (new, ~150 lines)
- `server/services/diagnostics_service.py` — health, metrics, LLM status (new, ~200 lines)
- `server/services/message_bus_service.py` — publish, subscribe, topics, DLQ, retry (new, ~200 lines)
- `server/services/sfe_dispatch_service.py` — SFE analysis dispatch, _dispatch_to_sfe (new, ~200 lines)
- `server/services/omnicore_service.py` — remove extracted methods, add delegate calls

### Changes

Each service accepts `ServiceContext` as a constructor parameter:
```python
class AdminService:
    def __init__(self, ctx: ServiceContext):
        self._ctx = ctx
```

**Method → Service mapping (small domains):**

| Service | Methods Extracted | Est. Lines |
|---------|-----------------|------------|
| `AdminService` | `_configure_llm`, `get_plugin_status`, `reload_plugin`, `browse_marketplace`, `install_plugin`, `query_database`, `export_database`, `get_circuit_breakers`, `reset_circuit_breaker`, `configure_rate_limit` | ~250 |
| `AuditQueryService` | `get_audit_trail`, `_read_audit_logs_from_files`, `start_periodic_audit_flush` | ~150 |
| `DiagnosticsService` | `get_llm_status`, `get_system_status`, `_check_agent_available`, `get_system_health`, `get_job_metrics` | ~200 |
| `MessageBusService` | `start_message_bus`, `publish_message`, `emit_event`, `subscribe_to_topic`, `unsubscribe`, `list_topics`, `query_dead_letter_queue`, `retry_message` | ~200 |
| `SFEDispatchService` | `_dispatch_sfe_action`, `_run_sfe_analysis`, `_dispatch_to_sfe` | ~200 |

**`omnicore_service.py`** drops to ~7,500 lines (generator pipeline + clarifier + routing remain).

### Unit Tests

- `server/tests/test_admin_service.py`:
  - `test_plugin_status_returns_registry` — verify plugin status
  - `test_circuit_breaker_reset` — verify reset propagates

- `server/tests/test_message_bus_service.py`:
  - `test_publish_message_routes_to_bus` — verify delegation
  - `test_subscribe_creates_handler` — verify subscription

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
```

---

## Phase 3: Decompose generator pipeline into sub-services

Split the ~3,500 lines of generator pipeline methods into 4 focused modules.

### Affected Files

- `server/tests/test_codegen_service.py` — verify codegen extraction
- `server/tests/test_deploy_service.py` — verify deploy extraction
- `server/services/pipeline/codegen_service.py` — code generation (new, ~250 lines)
- `server/services/pipeline/deploy_service.py` — deployment generation (new, ~250 lines)
- `server/services/pipeline/quality_service.py` — testgen + critique + docgen (new, ~250 lines)
- `server/services/pipeline/pipeline_orchestrator.py` — full pipeline sequencing + finalization (new, ~250 lines)
- `server/services/pipeline/__init__.py` — re-exports (new)
- `server/services/omnicore_service.py` — remove pipeline methods, delegate to orchestrator

### Changes

**Sub-service decomposition:**

| Module | Methods Extracted | Est. Lines |
|--------|-----------------|------------|
| `pipeline/codegen_service.py` | `_run_codegen`, `_execute_codegen` | ~250 |
| `pipeline/deploy_service.py` | `_run_deploy`, `_run_deploy_all`, `_execute_deploy_all_targets`, `_validate_deployment_completeness` | ~250 |
| `pipeline/quality_service.py` | `_run_testgen`, `_run_docgen`, `_run_critique` | ~250 |
| `pipeline/pipeline_orchestrator.py` | `_run_full_pipeline`, `_dispatch_generator_action`, `_finalize_successful_job`, `_finalize_failed_job`, `_create_artifact_zip` | ~250 |

The orchestrator composes the three sub-services:
```python
class PipelineOrchestrator:
    def __init__(self, ctx: ServiceContext):
        self._codegen = CodegenService(ctx)
        self._deploy = DeployService(ctx)
        self._quality = QualityService(ctx)
```

### Unit Tests

- `server/tests/test_codegen_service.py`:
  - `test_run_codegen_calls_agent` — verify LLM agent invocation
  - `test_codegen_handles_empty_response` — verify graceful handling

- `server/tests/test_deploy_service.py`:
  - `test_deploy_all_sequences_targets` — verify all targets executed
  - `test_validation_checks_artifacts` — verify completeness check

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
```

---

## Phase 4: Decompose clarifier into sub-modules

Split the ~4,000 lines of clarification methods into 3 focused modules.

### Affected Files

- `server/tests/test_question_generator.py` — verify question generation extraction
- `server/services/clarifier/question_generator.py` — LLM-driven question generation (new, ~250 lines)
- `server/services/clarifier/response_processor.py` — answer processing + requirements synthesis (new, ~250 lines)
- `server/services/clarifier/session_manager.py` — session lifecycle + cleanup (new, ~150 lines)
- `server/services/clarifier/__init__.py` — ClarifierService facade composing the three (new, ~100 lines)
- `server/services/omnicore_service.py` — remove clarifier methods, delegate

### Changes

**Sub-module decomposition:**

| Module | Methods Extracted | Est. Lines |
|--------|-----------------|------------|
| `clarifier/question_generator.py` | `_generate_clarification_questions` (the 3,763-line method — must be internally decomposed into prompt construction, LLM call, response parsing) | ~250 |
| `clarifier/response_processor.py` | `_submit_clarification_response`, `_generate_clarified_requirements`, `_categorize_answer`, `_get_clarification_feedback` | ~250 |
| `clarifier/session_manager.py` | `cleanup_expired_clarification_sessions`, `start_periodic_session_cleanup`, `_run_clarifier` (session orchestration) | ~150 |
| `clarifier/__init__.py` | `ClarifierService` composing the three sub-modules | ~100 |

**Critical**: `_generate_clarification_questions` is a single 3,763-line method. During extraction, it must be internally decomposed into:
- Prompt construction (build the LLM prompt from requirements)
- LLM invocation (call the provider, handle retries)
- Response parsing (extract structured questions from LLM output)
- Domain categorization (sort questions by domain)

Each of these becomes a private function within `question_generator.py`.

### Unit Tests

- `server/tests/test_question_generator.py`:
  - `test_generates_questions_from_requirements` — verify question output structure
  - `test_categorizes_by_domain` — verify domain sorting

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
```

---

## Phase 5: Migrate routers + delete facade

Remove `OmniCoreService` class. Each router imports its specific service directly.

### Affected Files

- `server/tests/test_router_service_injection.py` — verify each router gets correct service
- `server/routers/omnicore.py` — inject `AdminService`, `MessageBusService`, `DiagnosticsService`
- `server/routers/audit.py` — inject `AuditQueryService`
- `server/routers/generator.py` — inject `PipelineOrchestrator`
- `server/routers/jobs.py` — inject `PipelineOrchestrator`
- `server/routers/events.py` — inject `MessageBusService`
- `server/routers/jobs_ws.py` — inject `MessageBusService`
- `server/routers/diagnostics.py` — inject `DiagnosticsService`
- `server/routers/v1_compat.py` — inject `PipelineOrchestrator`
- `server/routers/fixes.py` — inject appropriate service
- `server/services/omnicore_service.py` — reduce to `route_job` dispatcher (~50 lines) + singleton accessors

### Changes

Router migration pattern:
```python
# Before
from server.services.omnicore_service import get_omnicore_service
omnicore_service: OmniCoreService = Depends(get_omnicore_service)
result = await omnicore_service.get_audit_trail(...)

# After
from server.services.audit_query_service import get_audit_query_service
audit_service: AuditQueryService = Depends(get_audit_query_service)
result = await audit_service.get_audit_trail(...)
```

Each service module provides its own `get_*_service()` singleton accessor. All singletons share a common `ServiceContext` instance created once at app startup.

`events.py` stops accessing `_message_bus` (private attr) — uses `MessageBusService.subscribe()` / `.unsubscribe()` public methods.

### Unit Tests

- `server/tests/test_router_service_injection.py`:
  - `test_audit_router_uses_audit_service` — verify injection
  - `test_events_router_uses_message_bus_service` — no private attr access
  - `test_generator_router_uses_pipeline_orchestrator` — verify injection
  - `test_omnicore_router_uses_admin_service` — verify injection

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
pytest tests/ -v --maxfail=5
```

---

## Summary

| Phase | Deliverable | omnicore_service.py Lines | New Files |
|-------|------------|--------------------------|-----------|
| 1 | ServiceContext + 4 helper modules | ~9,500 | 6 |
| 2 | 5 small domain services | ~7,500 | 5 |
| 3 | 4 pipeline sub-services | ~4,000 | 5 |
| 4 | 3 clarifier sub-modules | ~250 (facade) | 4 |
| 5 | Router migration, facade deleted | ~50 (dispatcher only) | 0 |

**Final file inventory (14 new modules):**

| File | Purpose | Est. Lines |
|------|---------|------------|
| `service_context.py` | Shared runtime state | ~120 |
| `helpers/validation.py` | Report/structure validators | ~120 |
| `helpers/project_detection.py` | Language detection, test checks | ~80 |
| `helpers/fallback_generators.py` | README/frontend/critique fallbacks | ~250 |
| `helpers/file_utils.py` | Path resolution, imports, nesting | ~120 |
| `admin_service.py` | Plugins, DB, circuit breakers | ~250 |
| `audit_query_service.py` | Audit trail queries | ~150 |
| `diagnostics_service.py` | Health, metrics, status | ~200 |
| `message_bus_service.py` | Pub/sub, DLQ, topics | ~200 |
| `sfe_dispatch_service.py` | SFE analysis dispatch | ~200 |
| `pipeline/codegen_service.py` | Code generation | ~250 |
| `pipeline/deploy_service.py` | Deployment generation | ~250 |
| `pipeline/quality_service.py` | Testgen + critique + docgen | ~250 |
| `pipeline/pipeline_orchestrator.py` | Full pipeline sequencing | ~250 |
| `clarifier/question_generator.py` | LLM question generation | ~250 |
| `clarifier/response_processor.py` | Answer processing + synthesis | ~250 |
| `clarifier/session_manager.py` | Session lifecycle | ~150 |
| `clarifier/__init__.py` | ClarifierService composition | ~100 |

**All files <= 250 lines. No `await` in `__init__` — all services accept `ServiceContext` as a parameter.**

# Plan: Decompose omnicore_service.py (11,021 lines → 7 focused services)

## Open Questions

- **Shared init state**: `OmniCoreService.__init__` initializes LLM config, agents, Kafka, and OmniCore components in one block. The new services need access to the same initialized state (message bus, agent refs, LLM config). Should we use a shared `ServiceContext` dataclass passed to each service, or should each service initialize its own slice? **Recommendation**: `ServiceContext` — avoids re-initialization, single source of truth.

## Phase 1: Extract ServiceContext and module-level helpers

Decouple shared state from the god-class. Extract the 16 module-level helper functions into a focused utilities module, and create `ServiceContext` to hold the shared runtime state.

### Affected Files

- `server/tests/test_service_context.py` — verify context initialization and access
- `server/services/service_context.py` — shared runtime state (new)
- `server/services/service_helpers.py` — extracted module-level functions (new)
- `server/services/omnicore_service.py` — import helpers, delegate init to context

### Changes

**`server/services/service_context.py`** (new, ~120 lines):
```python
@dataclass
class ServiceContext:
    """Shared runtime state for all decomposed services."""
    llm_config: dict
    agents: dict  # name -> loaded agent module
    message_bus: Optional[Any]  # ShardedMessageBus or None
    omnicore_engine: Optional[Any]  # OmniCoreEngine or None
    omnicore_components_available: dict  # component -> bool
    job_output_base: Path
    kafka_producer: Optional[Any]
```

Factory function:
```python
async def create_service_context(llm_config: dict = None) -> ServiceContext:
    """Initialize shared context once at startup."""
```

**`server/services/service_helpers.py`** (new, ~250 lines):

Move these 16 module-level functions out of `omnicore_service.py`:
- `_pre_materialization_import_check`
- `_detect_project_language`
- `_is_test_file`
- `_ensure_python_package_structure`
- `_load_readme_from_disk`
- `_extract_project_name_from_path_or_payload`
- `_generate_fallback_readme`
- `_generate_fallback_frontend_files`
- `_create_placeholder_critique_report`
- `_validate_report_structure`
- `_validate_helm_chart_structure`
- `_load_sfe_analysis_report`
- `_invalidate_sfe_analysis_cache`
- `_fix_double_nesting`
- `_build_delta_prompt`
- `_is_third_party_import_error`

**`server/services/omnicore_service.py`**:
- Replace all 16 inline functions with imports from `service_helpers`
- Replace `__init__` body with `ServiceContext` creation
- Store `self._ctx` as the shared context

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

## Phase 2: Extract domain services (generator, clarifier, admin, audit, diagnostics, message bus)

Split the 62 methods of `OmniCoreService` into 6 focused service classes, each receiving `ServiceContext`.

### Affected Files

- `server/tests/test_generator_pipeline_service.py` — verify pipeline extraction
- `server/tests/test_admin_service.py` — verify admin ops extraction
- `server/services/generator_pipeline_service.py` — codegen, testgen, deploy, docgen, critique, full pipeline (new)
- `server/services/clarifier_service.py` — question generation, responses, session management (new)
- `server/services/admin_service.py` — plugins, database, circuit breakers, rate limits (new)
- `server/services/audit_query_service.py` — audit trail queries, audit log reading (new)
- `server/services/diagnostics_service.py` — health, metrics, LLM status, system status (new)
- `server/services/message_bus_service.py` — publish, subscribe, topics, DLQ, retry (new)
- `server/services/omnicore_service.py` — becomes thin facade delegating to above

### Changes

Each new service follows this pattern:
```python
class GeneratorPipelineService:
    def __init__(self, ctx: ServiceContext):
        self._ctx = ctx

    async def run_codegen(self, job_id: str, payload: dict) -> dict:
        ...  # extracted from OmniCoreService._run_codegen
```

**Method → Service mapping:**

| Service | Methods Extracted | Est. Lines |
|---------|-----------------|------------|
| `GeneratorPipelineService` | `_run_codegen`, `_execute_codegen`, `_run_testgen`, `_run_deploy`, `_run_deploy_all`, `_execute_deploy_all_targets`, `_validate_deployment_completeness`, `_run_docgen`, `_run_critique`, `_run_full_pipeline`, `_dispatch_generator_action`, `_finalize_successful_job`, `_finalize_failed_job`, `_create_artifact_zip` | ~3,500 |
| `ClarifierService` | `_run_clarifier`, `_generate_clarification_questions`, `_get_clarification_feedback`, `_submit_clarification_response`, `_generate_clarified_requirements`, `_categorize_answer`, `cleanup_expired_clarification_sessions`, `start_periodic_session_cleanup` | ~4,000 |
| `AdminService` | `_configure_llm`, `get_plugin_status`, `reload_plugin`, `browse_marketplace`, `install_plugin`, `query_database`, `export_database`, `get_circuit_breakers`, `reset_circuit_breaker`, `configure_rate_limit` | ~750 |
| `AuditQueryService` | `get_audit_trail`, `_read_audit_logs_from_files`, `start_periodic_audit_flush` | ~250 |
| `DiagnosticsService` | `get_llm_status`, `get_system_status`, `_check_agent_available`, `get_system_health`, `get_job_metrics` | ~250 |
| `MessageBusService` | `start_message_bus`, `publish_message`, `emit_event`, `subscribe_to_topic`, `list_topics`, `query_dead_letter_queue`, `retry_message` | ~300 |

**`omnicore_service.py`** becomes a ~250-line facade:
```python
class OmniCoreService:
    """Facade — delegates to domain services. Will be removed once routers migrate."""

    def __init__(self):
        self._ctx = await create_service_context()
        self._generator = GeneratorPipelineService(self._ctx)
        self._clarifier = ClarifierService(self._ctx)
        self._admin = AdminService(self._ctx)
        self._audit = AuditQueryService(self._ctx)
        self._diagnostics = DiagnosticsService(self._ctx)
        self._bus = MessageBusService(self._ctx)

    async def route_job(self, ...):
        # Routing logic stays here — dispatches to _generator or _clarifier
        ...

    # Delegate methods:
    async def get_audit_trail(self, **kw): return await self._audit.get_audit_trail(**kw)
    async def get_system_health(self): return await self._diagnostics.get_system_health()
    # ... one-liner delegates for all 62 methods
```

### Unit Tests

- `server/tests/test_generator_pipeline_service.py`:
  - `test_run_codegen_delegates_to_agent` — verify codegen calls LLM agent
  - `test_run_full_pipeline_sequences_all_stages` — verify pipeline ordering
  - `test_finalize_job_creates_zip` — verify artifact creation

- `server/tests/test_admin_service.py`:
  - `test_plugin_status_returns_registry` — verify plugin status
  - `test_circuit_breaker_reset` — verify reset propagates

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
```

---

## Phase 3: Migrate routers to direct service injection

Remove the facade. Each router imports and depends on its specific service.

### Affected Files

- `server/tests/test_router_service_injection.py` — verify each router gets correct service
- `server/routers/omnicore.py` — inject `AdminService`, `MessageBusService`, `DiagnosticsService`, `AuditQueryService`
- `server/routers/audit.py` — inject `AuditQueryService`
- `server/routers/generator.py` — inject `GeneratorPipelineService`
- `server/routers/jobs.py` — inject `GeneratorPipelineService`
- `server/routers/events.py` — inject `MessageBusService`
- `server/routers/jobs_ws.py` — inject `MessageBusService`
- `server/routers/diagnostics.py` — inject `DiagnosticsService`
- `server/routers/sfe.py` — no change (already uses `SFEService`)
- `server/routers/v1_compat.py` — inject `GeneratorPipelineService`
- `server/routers/fixes.py` — inject appropriate service
- `server/services/omnicore_service.py` — delete facade class, keep only `route_job` as standalone dispatcher + singleton accessors for individual services

### Changes

Each router changes from:
```python
from server.services.omnicore_service import get_omnicore_service
omnicore_service: OmniCoreService = Depends(get_omnicore_service)
result = await omnicore_service.get_audit_trail(...)
```

To:
```python
from server.services.audit_query_service import get_audit_query_service
audit_service: AuditQueryService = Depends(get_audit_query_service)
result = await audit_service.get_audit_trail(...)
```

Each new service module provides its own `get_*_service()` singleton accessor following the same `Depends()` pattern.

The `events.py` router stops accessing `_message_bus` (private attr) and instead uses `MessageBusService.subscribe()` / `.unsubscribe()` public methods.

### Unit Tests

- `server/tests/test_router_service_injection.py`:
  - `test_omnicore_router_uses_admin_service` — verify injection
  - `test_audit_router_uses_audit_service` — verify injection
  - `test_events_router_uses_message_bus_service` — verify no more private attr access
  - `test_generator_router_uses_pipeline_service` — verify injection

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
pytest tests/ -v --maxfail=5
```

---

## Summary

| Phase | Deliverable | omnicore_service.py Lines |
|-------|------------|--------------------------|
| 1 | ServiceContext + helpers extracted | ~9,500 (helpers removed) |
| 2 | 6 domain services + facade | ~250 (facade only) |
| 3 | Routers migrated, facade removed | **0** (deleted or ~50 for route_job) |

| New File | Purpose | Est. Lines |
|----------|---------|------------|
| `service_context.py` | Shared runtime state | ~120 |
| `service_helpers.py` | Pure utility functions | ~250 |
| `generator_pipeline_service.py` | Codegen, testgen, deploy, docgen, critique | ~3,500* |
| `clarifier_service.py` | Q&A generation, sessions | ~4,000* |
| `admin_service.py` | Plugins, DB, circuit breakers | ~750 |
| `audit_query_service.py` | Audit trail queries | ~250 |
| `diagnostics_service.py` | Health, metrics, status | ~250 |
| `message_bus_service.py` | Pub/sub, DLQ, topics | ~300 |

*`generator_pipeline_service.py` and `clarifier_service.py` will themselves need further decomposition in a later phase to meet the 250-line target. This plan focuses on getting them out of the god-module first.

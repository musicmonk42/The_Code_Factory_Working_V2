# Plan: Router Migration + Facade Removal (DEC-5 + DEC-6)

## Open Questions

- **`route_job` ownership**: `route_job` is the central dispatcher used by `audit.py` and internally by `GeneratorService`. It routes between generator and SFE actions. Should it stay in `omnicore_service.py` as a standalone function, or move to `PipelineOrchestrator`? **Recommendation**: Move to a new `server/services/job_router.py` — it's a cross-cutting dispatcher, not part of any single domain service.

## Phase 1: Migrate routers to domain services

Each router switches from `Depends(get_omnicore_service)` to the specific service it actually needs.

### Affected Files

- `server/tests/test_router_migration.py` — verify each router imports from new service (new)
- `server/routers/omnicore.py` — inject AdminService, MessageBusService, DiagnosticsService, AuditQueryService
- `server/routers/audit.py` — inject AuditQueryService + job_router
- `server/routers/events.py` — inject MessageBusService (replace private `_message_bus` access)
- `server/routers/jobs_ws.py` — inject MessageBusService (replace private `_message_bus` access)
- `server/routers/jobs.py` — keep GeneratorService, replace OmniCoreService import for emit_event → MessageBusService
- `server/routers/v1_compat.py` — keep GeneratorService, replace OmniCoreService import for emit_event → MessageBusService
- `server/routers/diagnostics.py` — inject DiagnosticsService
- `server/routers/fixes.py` — remove unused OmniCoreService import
- `server/routers/sfe.py` — already uses SFEService, remove OmniCoreService import
- `server/services/job_router.py` — extract `route_job` dispatcher (new, ~100 lines)

### Changes

**`server/routers/omnicore.py`** — the largest migration (18 endpoints):

```python
# BEFORE
from server.services.omnicore_service import get_omnicore_service
omnicore_service: OmniCoreService = Depends(get_omnicore_service)

# AFTER — multiple services injected per endpoint
from server.services.admin_service import get_admin_service, AdminService
from server.services.message_bus_service import get_message_bus_service, MessageBusService
from server.services.diagnostics_service import get_diagnostics_service, DiagnosticsService
from server.services.audit_query_service import get_audit_query_service, AuditQueryService
```

Endpoint → service mapping:
| Endpoint | Method Called | New Service |
|----------|-------------|-------------|
| `GET /plugins` | `get_plugin_status` | `AdminService` |
| `GET /metrics/{job_id}` | `get_job_metrics` | `DiagnosticsService` |
| `GET /audit/{job_id}` | `get_audit_trail` | `AuditQueryService` |
| `GET /health` | `get_system_health` | `DiagnosticsService` |
| `POST /workflow` | `trigger_workflow` | `AdminService` |
| `POST /publish` | `publish_message` | `MessageBusService` |
| `POST /subscribe` | `subscribe_to_topic` | `MessageBusService` |
| `GET /topics` | `list_topics` | `MessageBusService` |
| `POST /plugins/reload` | `reload_plugin` | `AdminService` |
| `GET /marketplace` | `browse_marketplace` | `AdminService` |
| `POST /plugins/install` | `install_plugin` | `AdminService` |
| `POST /query` | `query_database` | `AdminService` |
| `POST /export` | `export_database` | `AdminService` |
| `GET /circuit-breakers` | `get_circuit_breakers` | `AdminService` |
| `POST /circuit-breakers/reset` | `reset_circuit_breaker` | `AdminService` |
| `POST /rate-limit` | `configure_rate_limit` | `AdminService` |
| `GET /dlq` | `query_dead_letter_queue` | `MessageBusService` |
| `POST /dlq/retry` | `retry_message` | `MessageBusService` |

**`server/routers/events.py`** — replace private attribute access:
```python
# BEFORE
bus = omnicore_service._message_bus
omnicore_service._message_bus.subscribe(topic, handler)

# AFTER
from server.services.message_bus_service import get_message_bus_service
bus_service = get_message_bus_service()
await bus_service.subscribe_to_topic(topic, handler)
```

**`server/routers/jobs.py` and `v1_compat.py`** — replace `emit_event`:
```python
# BEFORE
await omnicore_service.emit_event("job.created", {...})

# AFTER
from server.services.message_bus_service import get_message_bus_service
bus_service = get_message_bus_service()
await bus_service.emit_event("job.created", {...})
```

**`server/services/job_router.py`** (new, ~150 lines):
Extract and decompose `route_job` (235 lines in original) into focused sub-functions:

```python
def _make_route_result(job_id, source, target, transport, *, routed=True, data=None, error=None):
    """Build standardized routing result dict (~10 lines)."""

async def _dispatch_and_wrap(dispatch_fn, job_id, action, payload, source, target, transport):
    """Call dispatch_fn, wrap result in route_result. Handles errors (~15 lines)."""

async def _route_via_message_bus(ctx, job_id, source, target, payload):
    """Publish to message bus + audit log (~30 lines). Returns result or None on failure."""

async def route_job(ctx, job_id, source_module, target_module, payload):
    """Thin dispatcher (~25 lines):
    1. If target == generator → direct dispatch to pipeline
    2. If action == query_audit_logs → direct dispatch to SFE
    3. Try message bus
    4. Fallback: direct dispatch by target_module
    """
```

The original 235-line method contains 8 nearly-identical response dicts and 2 duplicated dispatch blocks. Decomposition eliminates the duplication via `_make_route_result` and `_dispatch_and_wrap` helpers. Each function <= 30 lines. Total file ~150 lines.

### Unit Tests

- `server/tests/test_router_migration.py`:
  - `test_omnicore_router_no_omnicore_service_import` — grep for old import, verify absent
  - `test_audit_router_uses_audit_service` — verify import chain
  - `test_events_router_no_private_attr_access` — grep for `._message_bus`, verify absent
  - `test_jobs_router_uses_bus_for_events` — verify MessageBusService import
  - `test_job_router_dispatches_to_pipeline` — verify route_job routes generator actions

### CI Validation

```bash
pytest server/tests/test_router_migration.py -v
pytest server/tests/ -v --maxfail=5
```

---

## Phase 2: Delete facade + slim omnicore_service.py

Remove the `OmniCoreService` class entirely. Replace with thin module exposing only `route_job` + singleton accessors for individual services.

### Affected Files

- `server/services/omnicore_service.py` — delete class, keep route_job import + service accessors
- `server/services/__init__.py` — update exports to use new service modules
- `server/main.py` — update startup to initialize ServiceContext + individual services
- `server/services/generator_service.py` — update to use job_router instead of OmniCoreService

### Changes

**`server/services/omnicore_service.py`** — reduce from 9,900 lines to ~80 lines:
```python
"""Compatibility shim — provides route_job and service accessors.

The OmniCoreService god-class has been decomposed. This module exists
for backward compatibility during the transition. Import directly from
the domain service modules instead.
"""
from server.services.job_router import route_job
from server.services.admin_service import get_admin_service
from server.services.audit_query_service import get_audit_query_service
from server.services.diagnostics_service import get_diagnostics_service
from server.services.message_bus_service import get_message_bus_service
from server.services.sfe_dispatch_service import get_sfe_dispatch_service
from server.services.pipeline import get_pipeline_orchestrator
from server.services.clarifier import get_clarifier_service
from server.services.service_context import ServiceContext, create_service_context

# Re-exports for backward compatibility
from server.services.helpers import *  # noqa: F401,F403

__all__ = [
    "route_job",
    "get_admin_service",
    "get_audit_query_service",
    "get_diagnostics_service",
    "get_message_bus_service",
    "get_sfe_dispatch_service",
    "get_pipeline_orchestrator",
    "get_clarifier_service",
    "ServiceContext",
    "create_service_context",
]
```

**`server/main.py`** — update startup initialization:
```python
# BEFORE
from server.services.omnicore_service import get_omnicore_service
omnicore = get_omnicore_service()

# AFTER
from server.services.service_context import create_service_context
ctx = await create_service_context()
# Individual services initialized via their singleton accessors
```

### Unit Tests

Existing tests from Phases 1-4 cover the new services. The primary verification is:
- All existing router tests still pass (behavior unchanged)
- `omnicore_service.py` is <= 80 lines
- No router imports `OmniCoreService` class

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
pytest tests/ -v --maxfail=5
```

---

## Summary

| Phase | Action | omnicore_service.py |
|-------|--------|-------------------|
| 1 | Migrate 10 routers to domain services | 9,900 (unchanged) |
| 2 | Delete facade class, keep compatibility shim | **~80 lines** |

| Router | Before | After |
|--------|--------|-------|
| `omnicore.py` | `OmniCoreService` (18 methods) | `AdminService` + `MessageBusService` + `DiagnosticsService` + `AuditQueryService` |
| `audit.py` | `OmniCoreService.route_job` + `get_audit_trail` | `job_router.route_job` + `AuditQueryService` |
| `events.py` | `OmniCoreService._message_bus` (private) | `MessageBusService` (public API) |
| `jobs_ws.py` | `OmniCoreService._message_bus` (private) | `MessageBusService` (public API) |
| `jobs.py` | `GeneratorService` + `OmniCoreService.emit_event` | `GeneratorService` + `MessageBusService` |
| `v1_compat.py` | `GeneratorService` + `OmniCoreService.emit_event` | `GeneratorService` + `MessageBusService` |
| `diagnostics.py` | `OmniCoreService` | `DiagnosticsService` |
| `fixes.py` | `OmniCoreService` (unused) | Remove import |
| `sfe.py` | `SFEService` (already correct) | No change |
| `generator.py` | `GeneratorService` | No change |

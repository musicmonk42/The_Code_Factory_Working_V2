# Plan: DEC-6 Unblock — Decouple main.py, GeneratorService, and __init__.py from OmniCoreService

## Open Questions

- **`main.py` line 1502**: `get_omnicore_service()` is called during agent loading. Need to verify what it's used for — if it's just passing the service to something, the fix is the same pattern. If it's calling methods, those need mapping to the right domain service.

## Phase 1: Decouple GeneratorService from OmniCoreService

`GeneratorService` calls `self.omnicore_service.route_job()` 15 times. Replace the OmniCoreService dependency with `route_job` from `job_router.py`.

### Affected Files

- `server/tests/test_generator_service_decoupled.py` — verify GeneratorService works with route_job (new)
- `server/services/generator_service.py` — replace omnicore_service param with route_job + ServiceContext
- `server/routers/jobs.py` — update GeneratorService construction
- `server/routers/v1_compat.py` — update GeneratorService construction
- `server/routers/generator.py` — update GeneratorService construction

### Changes

**`server/services/generator_service.py`**:

```python
# BEFORE (line 66)
def __init__(self, storage_path=None, omnicore_service=None):
    self.omnicore_service = omnicore_service

# AFTER
def __init__(self, storage_path=None, *, route_job_fn=None, ctx=None):
    self._route_job = route_job_fn
    self._ctx = ctx
```

Replace all 15 occurrences of:
```python
await self.omnicore_service.route_job(job_id, "generator", "generator", payload)
```
With:
```python
await self._route_job(self._ctx, job_id, "generator", "generator", payload)
```

**Router construction** (jobs.py, v1_compat.py, generator.py):
```python
# BEFORE
from server.services.omnicore_service import get_omnicore_service
omnicore = get_omnicore_service()
return GeneratorService(omnicore_service=omnicore)

# AFTER
from server.services.job_router import route_job
from server.services.service_context import ServiceContext
return GeneratorService(route_job_fn=route_job, ctx=get_service_context())
```

### Unit Tests

- `server/tests/test_generator_service_decoupled.py`:
  - `test_generator_service_accepts_route_job_fn` — verify construction with callable
  - `test_generator_service_no_omnicore_import` — grep source, verify no `omnicore_service` import

### CI Validation

```bash
pytest server/tests/test_generator_service_decoupled.py -v
```

---

## Phase 2: Decouple main.py startup/shutdown from OmniCoreService

`main.py` accesses `OmniCoreService._message_bus` at 3 locations (startup, agent loading, shutdown). Replace with `MessageBusService`.

### Affected Files

- `server/main.py` — replace 3 OmniCoreService coupling points with MessageBusService

### Changes

**Startup (lines 644-665)**:
```python
# BEFORE
from server.services.omnicore_service import get_omnicore_service
omnicore_service = get_omnicore_service()
if hasattr(omnicore_service, '_message_bus') and omnicore_service._message_bus:
    await omnicore_service.start_message_bus()

# AFTER
from server.services.message_bus_service import get_message_bus_service
bus_service = get_message_bus_service()
if bus_service.is_available():
    await bus_service.start_message_bus()
    # Verify startup with retry
    bus = bus_service.get_bus()
    for i in range(10):
        if hasattr(bus, '_dispatchers_started') and bus._dispatchers_started:
            break
        await asyncio.sleep(1)
```

**Agent loading (line 1502-1503)**:
Verify usage and replace with appropriate domain service.

**Shutdown (lines 2248-2252)**:
```python
# BEFORE
omnicore_service = get_omnicore_service()
if hasattr(omnicore_service, '_message_bus') and omnicore_service._message_bus:
    message_bus = omnicore_service._message_bus

# AFTER
bus_service = get_message_bus_service()
if bus_service.is_available():
    message_bus = bus_service.get_bus()
```

### Unit Tests

Existing `test_router_migration.py` already covers the pattern. Add:
- `test_main_no_omnicore_private_attr` — grep main.py for `._message_bus`, verify absent

### CI Validation

```bash
pytest server/tests/test_router_migration.py -v
```

---

## Phase 3: Slim __init__.py exports + reduce omnicore_service.py to compatibility shim

### Affected Files

- `server/services/__init__.py` — replace OmniCoreService exports with domain service exports
- `server/services/omnicore_service.py` — reduce to ~80-line compatibility shim (re-exports only)

### Changes

**`server/services/__init__.py`**:
```python
# BEFORE
from .omnicore_service import OmniCoreService, get_omnicore_service, get_omnicore_service_async

# AFTER
from .service_context import ServiceContext, create_service_context
from .job_router import route_job
from .admin_service import AdminService, get_admin_service
from .audit_query_service import AuditQueryService, get_audit_query_service
from .diagnostics_service import DiagnosticsService, get_diagnostics_service
from .message_bus_service import MessageBusService, get_message_bus_service
from .sfe_dispatch_service import SFEDispatchService, get_sfe_dispatch_service

# Backward compat — will be removed
from .omnicore_service import get_omnicore_service, get_omnicore_service_async
```

**`server/services/omnicore_service.py`** — reduce to compatibility shim (~80 lines):
- Delete the 9,900-line `OmniCoreService` class
- Keep `get_omnicore_service` and `get_omnicore_service_async` as thin wrappers that return a lightweight object (or deprecation warning)
- Re-export all helpers for backward compatibility

### Unit Tests

- `test_omnicore_shim_line_count` — verify file <= 100 lines
- `test_omnicore_shim_reexports_helpers` — verify backward-compatible imports still work

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
pytest tests/ -v --maxfail=5
```

---

## Summary

| Phase | Blocker Resolved | Key Change |
|-------|-----------------|------------|
| 1 | GeneratorService coupling | Replace `omnicore_service` param with `route_job_fn` + `ctx` |
| 2 | main.py coupling | Replace 3x `._message_bus` access with `MessageBusService` |
| 3 | __init__.py exports | Export domain services, slim omnicore_service.py to ~80 lines |

**End state**: `omnicore_service.py` goes from 9,900 lines to ~80 lines. No code imports `OmniCoreService` class directly. All routing goes through decomposed services.

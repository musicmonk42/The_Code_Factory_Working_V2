# Plan: God-Module Decomposition

## Open Questions

- **omnicore_service.py (11K lines)**: This is the largest file and the highest-risk decomposition. Should it be split by domain (job management, SFE orchestration, generator orchestration, audit, diagnostics) or by layer (service, repository, events)? Domain-first is simpler; layer-first is more conventional for FastAPI. **Recommendation**: Domain-first — each service class gets its own file.
- **Parallel vs sequential**: Can phases 1-3 be worked on in parallel by different contributors, or do they have ordering dependencies? **Answer**: Phases are independent (different modules). Can be parallelized.

## Phase 1: Decompose `server/services/omnicore_service.py` (11,021 lines)

The worst offender. This file is the entire OmniCore service layer crammed into one module.

### Affected Files

- `server/tests/test_omnicore_service_split.py` — verify imports resolve after split
- `server/services/omnicore_service.py` — split into domain-specific service files
- `server/services/__init__.py` — re-export for backwards compatibility during transition
- `server/services/job_service.py` — job lifecycle management (extracted)
- `server/services/generator_service.py` — generator orchestration (extracted)
- `server/services/sfe_service.py` — already exists, verify no duplication
- `server/services/audit_service.py` — audit operations (extracted)
- `server/services/diagnostics_service.py` — diagnostics/health (extracted)

### Changes

1. Identify natural domain boundaries in `omnicore_service.py` by scanning method groups
2. Extract each group into its own file with the same class interface
3. Create a facade in `omnicore_service.py` that delegates to sub-services (preserves API)
4. Verify all routers still resolve through the facade

### Unit Tests

- `server/tests/test_omnicore_service_split.py`:
  - `test_facade_delegates_to_job_service` — verify delegation works
  - `test_all_router_imports_resolve` — verify no broken imports after split
  - `test_service_methods_match_original` — verify public API unchanged

### CI Validation

```bash
pytest server/tests/ -v --maxfail=5
```

---

## Phase 2: Decompose `generator/agents/codegen_agent/` (12,570 lines across 2 files)

`codegen_agent.py` (5,926 lines) and `codegen_response_handler.py` (6,644 lines) are both god-modules.

### Affected Files

- `generator/tests/test_codegen_split.py` — verify imports after split
- `generator/agents/codegen_agent/codegen_agent.py` — split orchestration from generation
- `generator/agents/codegen_agent/codegen_response_handler.py` — split by output type
- `generator/agents/codegen_agent/code_parser.py` — code parsing/extraction (extracted)
- `generator/agents/codegen_agent/code_validator.py` — validation logic (extracted)
- `generator/agents/codegen_agent/language_handlers.py` — per-language handling (extracted)

### Changes

1. `codegen_agent.py`: Extract code generation orchestration vs. LLM interaction vs. file I/O
2. `codegen_response_handler.py`: Extract parsing, validation, and per-language handlers into separate modules
3. Keep `codegen_agent.py` as the orchestrator that composes the extracted pieces

### Unit Tests

- `generator/tests/test_codegen_split.py`:
  - `test_codegen_agent_imports_resolve` — verify no broken imports
  - `test_parser_extracts_code_blocks` — verify extracted parser works standalone
  - `test_validator_checks_syntax` — verify extracted validator works standalone

### CI Validation

```bash
pytest generator/tests/ -v --maxfail=5
```

---

## Phase 3: Decompose `self_fixing_engineer/arbiter/arbiter.py` (5,554 lines)

The Arbiter god-class contains RL orchestration, database management, crypto, HTTP clients, and metrics in a single file.

### Affected Files

- `self_fixing_engineer/tests/test_arbiter_split.py` — verify imports after split
- `self_fixing_engineer/arbiter/arbiter.py` — split into focused modules
- `self_fixing_engineer/arbiter/arbiter_core.py` — core orchestration (extracted)
- `self_fixing_engineer/arbiter/arbiter_rl.py` — RL/meta-learning logic (extracted)
- `self_fixing_engineer/arbiter/arbiter_db.py` — database operations (extracted)
- `self_fixing_engineer/arbiter/arbiter_http.py` — HTTP client operations (extracted)

### Changes

1. Identify the Arbiter class's method groups by domain
2. Extract RL logic (gymnasium, stable-baselines3) into `arbiter_rl.py`
3. Extract database operations into `arbiter_db.py`
4. Extract HTTP client operations into `arbiter_http.py`
5. Keep `arbiter.py` as the composition root importing from sub-modules

### Unit Tests

- `self_fixing_engineer/tests/test_arbiter_split.py`:
  - `test_arbiter_imports_resolve` — verify no broken imports
  - `test_arbiter_rl_standalone` — verify RL module works independently
  - `test_arbiter_db_standalone` — verify DB module works independently

### CI Validation

```bash
pytest self_fixing_engineer/tests/ -v --maxfail=5
```

---

## Phase 4: Decompose remaining files > 3,000 lines

Target the remaining 5 files exceeding 3,000 lines:

| File | Lines | Decomposition Strategy |
|------|-------|----------------------|
| `generator/runner/runner_file_utils.py` | 3,963 | Split by file operation type (read, write, validate, template) |
| `generator/agents/deploy_agent/deploy_response_handler.py` | 3,857 | Split by deploy target (Docker, Helm, K8s, compose) |
| `generator/runner/runner_core.py` | 3,593 | Split orchestration from execution from reporting |
| `generator/main/engine.py` | 3,560 | Split workflow phases into separate pipeline stages |
| `omnicore_engine/database/database.py` | 3,375 | Split DDL/migrations from CRUD from query builders |

Each follows the same pattern: identify domain boundaries, extract into focused modules, keep original as composition root.

---

## Summary

| Phase | Target | Lines Before | Target After | Files Created |
|-------|--------|-------------|-------------|---------------|
| 1 | `omnicore_service.py` | 11,021 | ~250 (facade) + 5 services | 5-6 |
| 2 | `codegen_agent/` | 12,570 | ~250 each + 3 extracted | 3-4 |
| 3 | `arbiter.py` | 5,554 | ~250 (root) + 3 extracted | 3-4 |
| 4 | 5 remaining files | 18,348 | ~250 each + extracted | 10-15 |

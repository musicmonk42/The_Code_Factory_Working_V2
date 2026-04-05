# Plan: Update ARCHITECTURE_PLAN.md to reflect actual codebase (#1791)

## Open Questions

None — the drift is well-documented from the research brief.

## Phase 1: Update file tree and interface contracts

### Affected Files

- `docs/ARCHITECTURE_PLAN.md` — rewrite file tree to match reality

### Changes

Replace the current file tree (covers ~40% of actual codebase) with the verified structure including:

**Additions to document**:
- `server/services/` decomposed structure (helpers/, pipeline/, clarifier/, 5 domain services)
- `server/routers/` — all 13 routers (was 6)
- `server/services/`, `server/schemas/`, `server/middleware/`, `server/utils/` layers
- `self_fixing_engineer/test_generation/` (27 files)
- `self_fixing_engineer/self_healing_import_fixer/` (22 files)
- `self_fixing_engineer/plugins/` (46 files, 13 sub-directories)
- `self_fixing_engineer/envs/` (RL environments)
- `self_fixing_engineer/arbiter/policy/` (policy management)
- `generator/specs/` (GDPR/HIPAA compliance templates)
- `config/` directory (from workspace reorganization)

**Corrections**:
- `generator/utils/llm_client.py` → actual location: `generator/runner/llm_client.py`
- `self_fixing_engineer/codebase_analyzer.py` → actual: `self_fixing_engineer/arbiter/codebase_analyzer.py`
- `self_fixing_engineer/bug_manager.py` → actual: `self_fixing_engineer/arbiter/bug_manager/bug_manager.py`

Update the decomposition progress note in Section 4 Razor Pre-Check.

### Unit Tests

None — documentation only.

### CI Validation

None — markdown only.

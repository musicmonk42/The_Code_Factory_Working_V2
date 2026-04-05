# Project Backlog

## Blockers (Must Fix Before Progress)

### Security Blockers

- [x] [S1] **CRITICAL**: Hardcoded JWT fallback secret in arena auth (Complete — fail-closed, raises HTTP 503 when ARENA_JWT_SECRET not configured)
- [x] [S2] **HIGH**: Sandbox validation accepts partial passes (Complete — requires `returncode == 0`, collection-error heuristic preserved)
- [x] [S3] **HIGH**: Destructive SQLite DB deletion on arena startup (Complete — `reset_db=False` by default, both async and sync paths patched)

### Development Blockers

- [x] [D1] **MEDIUM**: Broken retry-pipeline regression tests (Complete — mock_settings added to all 4 ArbiterArena calls)
- [ ] [D2] **MEDIUM**: Case-insensitive filename collision on Windows — repo tracks both `prompt_templates/README_default.jinja` and `prompt_templates/readme_default.jinja`. Git warns about the collision on case-insensitive filesystems, worktree comes up dirty, and the lowercase file is a one-line stub risking template resolution bugs. **Remediation**: Remove the lowercase stub; canonicalize to one casing.
- [x] [D3] **MEDIUM**: Auth decorator swallows 401/403 as 500 (Complete — `except HTTPException: raise` added before broad handler)

## Backlog (Planned Work)

- [x] [B1] Update README clone URL (Complete — executive README rewrite)
- [ ] [B2] Reconcile Python version requirement: README says 3.11+, `pyproject.toml:12` declares `>=3.10`
- [ ] [B3] Investigate `pytest --collect-only` timeout (>2 minutes) — likely import-time side effects or heavy fixtures
- [ ] [B4] Section 4 Razor compliance pass — many files exceed 250-line and 40-line function thresholds

### Decomposition Progress

- [x] [DEC-1] Phase 1: ServiceContext + 16 helpers extracted (9 files)
- [x] [DEC-2] Phase 2: 5 domain services extracted + helpers wired (6 files)
- [x] [DEC-3] Phase 3: Pipeline sub-services extracted (5 files)
- [x] [DEC-4] Phase 4: Clarifier sub-modules extracted (6 files)
- [x] [DEC-5] Phase 5: Router migration — 8 routers migrated to domain services, job_router.py created
- [x] [DEC-6] Phase 5: Decouple main.py, GeneratorService, __init__.py from OmniCoreService (facade kept as compat shim)
- [ ] [DEC-7] Internal decomposition of oversized methods (>40 lines)

### Documentation

- [x] [DOC-1] **HIGH**: Executive-level README.md upgrade (Complete — badges, TOC, architecture diagram, tiered install, 14 verified links)

## Wishlist (Nice to Have)

- [ ] [W1] Pre-commit hook for secret scanning as part of CI genesis
- [ ] [W2] Branch protection rules documentation for main/staging

---
_Updated by /qor-bootstrap genesis_

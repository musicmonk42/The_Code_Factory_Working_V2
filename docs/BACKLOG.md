# Project Backlog

## Blockers (Must Fix Before Progress)

### Security Blockers

- [x] [S1] **CRITICAL**: Hardcoded JWT fallback secret in arena auth (Complete — fail-closed, raises HTTP 503 when ARENA_JWT_SECRET not configured)
- [x] [S2] **HIGH**: Sandbox validation accepts partial passes (Complete — requires `returncode == 0`, collection-error heuristic preserved)
- [x] [S3] **HIGH**: Destructive SQLite DB deletion on arena startup (Complete — `reset_db=False` by default, both async and sync paths patched)

### Development Blockers

- [ ] [D1] **MEDIUM**: Broken retry-pipeline regression tests — `tests/test_sfe_retry_pipeline.py:81` and `:116` instantiate `ArbiterArena` without the now-required `settings` argument (`arena.py:320`). The SFE retry feature is effectively unprotected by tests. **Remediation**: Update test fixtures to pass required `settings` argument.
- [ ] [D2] **MEDIUM**: Case-insensitive filename collision on Windows — repo tracks both `prompt_templates/README_default.jinja` and `prompt_templates/readme_default.jinja`. Git warns about the collision on case-insensitive filesystems, worktree comes up dirty, and the lowercase file is a one-line stub risking template resolution bugs. **Remediation**: Remove the lowercase stub; canonicalize to one casing.
- [x] [D3] **MEDIUM**: Auth decorator swallows 401/403 as 500 (Complete — `except HTTPException: raise` added before broad handler)

## Backlog (Planned Work)

- [ ] [B1] Update README clone URL from old `The_Code_Factory_Working_V2` repo to current `MythologIQ/A.S.E.`
- [ ] [B2] Reconcile Python version requirement: README says 3.11+, `pyproject.toml:12` declares `>=3.10`
- [ ] [B3] Investigate `pytest --collect-only` timeout (>2 minutes) — likely import-time side effects or heavy fixtures
- [ ] [B4] Section 4 Razor compliance pass — many files exceed 250-line and 40-line function thresholds

### Decomposition Progress

- [x] [DEC-1] Phase 1: ServiceContext + 16 helpers extracted (9 files)
- [x] [DEC-2] Phase 2: 5 domain services extracted + helpers wired (6 files)
- [x] [DEC-3] Phase 3: Pipeline sub-services extracted (5 files)
- [x] [DEC-4] Phase 4: Clarifier sub-modules extracted (6 files)
- [ ] [DEC-5] Phase 5: Router migration — change Depends() to use new services
- [ ] [DEC-6] Phase 5: Delete facade — remove delegation stubs, inline methods
- [ ] [DEC-7] Internal decomposition of oversized methods (>40 lines)

## Wishlist (Nice to Have)

- [ ] [W1] Pre-commit hook for secret scanning as part of CI genesis
- [ ] [W2] Branch protection rules documentation for main/staging

---
_Updated by /qor-bootstrap genesis_

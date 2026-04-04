# Plan: Critical Security Fixes — Fail-Closed Secret Handling & Validation Hardening

## Open Questions

- **S2 collection-error heuristic**: Lines 1313-1324 of `sfe_service.py` accept a fix when it reduces collection errors even if tests fail. This is a deliberate design choice with a clear comment. Should this secondary acceptance path be preserved as-is, or should it also require `proc.returncode == 0`? **Plan preserves it** since it checks a genuine improvement metric (fewer collection errors), unlike the `passed > 0` clause which is the actual defect.

## Phase 1: Remove Hardcoded Secret Fallbacks (S1, S4, S5, HMAC)

All four findings share one anti-pattern: `os.environ.get("KEY", "hardcoded-fallback")`. The fix is identical in each case: remove the fallback, fail loudly at initialization.

### Affected Files

- `tests/test_security_fail_closed.py` — new test file validating all four fail-closed behaviors
- `self_fixing_engineer/arbiter/arena.py` — remove `JWT_SECRET_FALLBACK` (S1)
- `omnicore_engine/security_utils.py` — remove `"omnicore-default-secret"` (S4)
- `generator/main/api.py` — remove `"dev-secret-key-do-not-use-in-production"` (S5)
- `server/main.py` — remove hardcoded HMAC key default (HMAC)

### Changes

**`self_fixing_engineer/arbiter/arena.py`**

Line 201 — delete:
```python
JWT_SECRET_FALLBACK = "your-arena-jwt-secret-fallback-if-config-not-loaded"
```

Lines 296-300 — replace conditional fallback with fail-closed:
```python
# BEFORE
jwt_secret_value = (
    settings.ARENA_JWT_SECRET.get_secret_value()
    if settings.ARENA_JWT_SECRET
    else JWT_SECRET_FALLBACK
)

# AFTER
if not settings.ARENA_JWT_SECRET:
    raise HTTPException(
        status_code=503,
        detail="Arena authentication not configured. Set ARENA_JWT_SECRET.",
    )
jwt_secret_value = settings.ARENA_JWT_SECRET.get_secret_value()
```

**`omnicore_engine/security_utils.py`**

Lines 786-788 — replace fallback with fail-closed:
```python
# BEFORE
self._secret = secret or os.environ.get(
    "OMNICORE_SECRET", "omnicore-default-secret"
)

# AFTER
self._secret = secret or os.environ.get("OMNICORE_SECRET")
if not self._secret:
    raise RuntimeError(
        "OMNICORE_SECRET environment variable or 'secret' parameter is required. "
        "Cannot initialize EnterpriseSecurityUtils without a secret."
    )
```

**`generator/main/api.py`**

Lines 548-558 — remove dev fallback, fail-closed in all non-test modes:
```python
# BEFORE
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if (
    not SECRET_KEY and not _is_dev_or_test_mode() and _FASTAPI_AVAILABLE
):
    logger.critical(...)
    raise ValueError("JWT_SECRET_KEY environment variable not set.")
elif not SECRET_KEY:
    SECRET_KEY = "dev-secret-key-do-not-use-in-production"

# AFTER
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY and _FASTAPI_AVAILABLE:
    if _is_dev_or_test_mode():
        SECRET_KEY = os.urandom(32).hex()
        logger.warning(
            "JWT_SECRET_KEY not set — generated ephemeral key for dev/test. "
            "Sessions will not persist across restarts."
        )
    else:
        logger.critical("JWT_SECRET_KEY environment variable not set.")
        raise ValueError("JWT_SECRET_KEY environment variable not set.")
```

**`server/main.py`**

Lines 86-89 — remove hardcoded HMAC key, fail-closed:
```python
# BEFORE
os.environ.setdefault(
    "AGENTIC_AUDIT_HMAC_KEY",
    "7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a"
)

# AFTER
if not os.environ.get("AGENTIC_AUDIT_HMAC_KEY"):
    if _is_test_environment:
        os.environ["AGENTIC_AUDIT_HMAC_KEY"] = os.urandom(32).hex()
    else:
        raise RuntimeError(
            "AGENTIC_AUDIT_HMAC_KEY environment variable is required for audit log signing. "
            "Generate with: python -c \"import os; print(os.urandom(32).hex())\""
        )
```

### Unit Tests

- `tests/test_security_fail_closed.py` — Tests for each fail-closed path:
  - `test_arena_auth_rejects_without_jwt_secret` — Verify `require_auth` raises HTTP 503 when `ARENA_JWT_SECRET` is None
  - `test_omnicore_security_rejects_without_secret` — Verify `EnterpriseSecurityUtils()` raises `RuntimeError` without secret or env var
  - `test_generator_api_rejects_without_jwt_key` — Verify startup raises `ValueError` when `JWT_SECRET_KEY` unset and not dev/test mode
  - `test_generator_api_ephemeral_key_in_dev` — Verify dev mode generates random key (not hardcoded)
  - `test_server_rejects_without_hmac_key` — Verify startup raises `RuntimeError` when `AGENTIC_AUDIT_HMAC_KEY` unset and not test

### CI Validation

```bash
pytest tests/test_security_fail_closed.py -v
```

---

## Phase 2: Fix Sandbox Validation & Auth Decorator (S2, D3)

### Affected Files

- `server/tests/test_sfe_sandbox_validation.py` — new test for validation logic
- `server/services/sfe_service.py` — fix `passed > 0` acceptance (S2)
- `self_fixing_engineer/arbiter/arena.py` — fix auth decorator swallowing 401/403 (D3)

### Changes

**`server/services/sfe_service.py`**

Line 1303 — remove `or passed > 0`:
```python
# BEFORE
if proc.returncode == 0 or passed > 0:
    fix.validation_status = "validated"

# AFTER
if proc.returncode == 0:
    fix.validation_status = "validated"
```

The collection-error heuristic at lines 1313-1324 is preserved — it checks a genuine improvement metric (`baseline_collection_errors > 0 and post_fix_collection_errors < baseline_collection_errors`), which is a deliberate and justified acceptance path.

**`self_fixing_engineer/arbiter/arena.py`**

Lines 306-312 — re-raise `HTTPException` before broad except:
```python
# BEFORE
except jwt.InvalidTokenError:
    raise HTTPException(
        status_code=401, detail="Invalid or expired authentication token."
    )
except Exception as e:
    logger.error(f"Authentication failed: {e}", exc_info=True)
    raise HTTPException(status_code=500, detail="Authentication service error.")

# AFTER
except jwt.InvalidTokenError:
    raise HTTPException(
        status_code=401, detail="Invalid or expired authentication token."
    )
except HTTPException:
    raise
except Exception as e:
    logger.error(f"Authentication failed: {e}", exc_info=True)
    raise HTTPException(status_code=500, detail="Authentication service error.")
```

### Unit Tests

- `server/tests/test_sfe_sandbox_validation.py`:
  - `test_sandbox_rejects_partial_pass` — `returncode=1, passed=3, failed=7` must NOT validate
  - `test_sandbox_accepts_clean_pass` — `returncode=0` must validate
  - `test_sandbox_accepts_collection_error_improvement` — baseline errors > post-fix errors must validate (preserving existing behavior)
  - `test_sandbox_rejects_zero_pass_zero_return` — `returncode=1, passed=0` must NOT validate

- `self_fixing_engineer/tests/test_arena_auth_decorator.py`:
  - `test_auth_returns_401_not_500_for_missing_token` — Verify 401 propagates
  - `test_auth_returns_403_not_500_for_insufficient_role` — Verify 403 propagates
  - `test_auth_returns_500_for_unexpected_error` — Verify non-HTTP exceptions become 500

### CI Validation

```bash
pytest server/tests/test_sfe_sandbox_validation.py self_fixing_engineer/tests/test_arena_auth_decorator.py -v
```

---

## Phase 3: Fix Destructive DB Deletion (S3)

### Affected Files

- `self_fixing_engineer/tests/test_arena_db_preservation.py` — new test for DB preservation
- `self_fixing_engineer/arbiter/arena.py` — preserve DB by default, add `--reset-db` flag (TWO code paths)

### Changes

**`self_fixing_engineer/arbiter/arena.py`**

The destructive deletion exists in **two** code paths that must both be patched:

**Site 1: `run_arena_async()` — Lines 1518-1524:**
```python
# BEFORE
if os.path.exists(db_file):
    try:
        os.remove(db_file)
        logger.info(f"Cleaned up existing DB file: {db_file}")
    except OSError as e:
        logger.warning(f"Could not remove existing DB file {db_file}: {e}")
        arena_errors_total.labels(error_type="db_cleanup_fail").inc()

# AFTER
if os.path.exists(db_file):
    if reset_db:
        try:
            os.remove(db_file)
            logger.info(f"Reset DB: removed existing file {db_file}")
        except OSError as e:
            logger.warning(f"Could not remove existing DB file {db_file}: {e}")
            arena_errors_total.labels(error_type="db_cleanup_fail").inc()
    else:
        logger.info(f"Preserving existing DB file: {db_file}")
```

**Site 2: Lines 1612-1618** (identical pattern, same fix applied).

**Signature changes:**
- `run_arena_async(settings=None)` -> `run_arena_async(settings=None, *, reset_db: bool = False)`
- `run_arena()` -> `run_arena(reset_db: bool = False)` — propagates to `run_arena_async()`

**Call site propagation:**
- `self_fixing_engineer/run_sfe.py:113` and `:152` — add `reset_db=False` (preserve default)
- CLI entry at `arena.py:1681` — no change needed (default False preserves existing behavior)

### Unit Tests

- `self_fixing_engineer/tests/test_arena_db_preservation.py`:
  - `test_run_arena_preserves_existing_db_by_default` — DB file exists before call, still exists after
  - `test_run_arena_deletes_db_with_reset_flag` — DB file exists before call, deleted when `reset_db=True`
  - `test_run_arena_creates_db_when_none_exists` — No DB file before call, created after

### CI Validation

```bash
pytest self_fixing_engineer/tests/test_arena_db_preservation.py -v
```

---

## Summary

| Phase | Fixes | Files Changed | Tests Added |
|-------|-------|---------------|-------------|
| 1 | S1, S4, S5, HMAC | 4 source + 1 test | 5 test cases |
| 2 | S2, D3 | 2 source + 2 test | 7 test cases |
| 3 | S3 | 1 source + 1 test | 3 test cases |
| **Total** | **7 fixes** | **7 source + 4 test** | **15 test cases** |

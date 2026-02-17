# Deployment Configuration Review - Token Limit and Import Path Fixes

## Summary

This document reviews the deployment configuration files (Docker, Kubernetes, Helm, Makefile) following the 5 interrelated fixes applied to the codebase. **No changes are required** to deployment configurations, but this document serves as a reference for operators.

## Changes Made to Code

### 1. MAX_PROMPT_TOKENS Increased from 8192 to 16000
- **Files Changed:**
  - `generator/agents/critique_agent/critique_prompt.py` (lines 166 and 917)
  - `generator/agents/codegen_agent/codegen_prompt.py` (module constant)
- **Impact:** Allows larger prompts for code generation and critique, reducing premature truncation
- **Environment Variable Support:** 
  - `TESTGEN_MAX_PROMPT_TOKENS` - Already supported in testgen agents (default: 16000)
  - No env var for codegen/critique agents - hardcoded to 16000

### 2. Tiktoken Encoding Fixed for GPT-4o
- **Files Changed:**
  - `generator/runner/llm_client.py` (module-level `count_tokens` function and `LLMClient.count_tokens` method)
- **Impact:** Uses correct `o200k_base` encoding for GPT-4o models, `cl100k_base` for GPT-3.5/4, with fallback
- **No Configuration Needed:** Automatically detects model type

### 3. LLM max_tokens Increased from 500 to 4096
- **Files Changed:**
  - `self_fixing_engineer/arbiter/config.py` (LLMSettings class)
- **Impact:** Allows longer LLM responses for code fixes, deployment configs, and test files
- **Environment Variable:** `LLM_MAX_TOKENS` (default: 4096 if not set)
- **Deployment Config:** Not currently set in Helm/K8s - uses Python default (4096)

### 4. BaseHTTPMiddleware Import Instructions Added
- **Files Changed:**
  - `generator/agents/codegen_agent/codegen_prompt.py` (syntax safety instructions)
  - `generator/agents/codegen_agent/templates/python.jinja2` (FastAPI template)
- **Impact:** Prevents generation of code with incorrect `fastapi.middleware.base` import
- **No Configuration Needed:** This is a prompt engineering fix

### 5. Import Fixer Handles fastapi.middleware.base → starlette.middleware.base
- **Files Changed:**
  - `self_fixing_engineer/self_healing_import_fixer/import_fixer/import_fixer_engine.py`
- **Impact:** Automatically fixes incorrect BaseHTTPMiddleware imports in existing code
- **No Configuration Needed:** Runs automatically during code fixing pipeline

## Deployment Configuration Review

### ✅ Dockerfile
**Status:** No changes required
- Already includes `starlette` in dependencies (verified with import check)
- No hardcoded token limits
- All fixes are code-level, not build-time

### ✅ Kubernetes ConfigMaps
**Status:** No changes required
**Files Reviewed:**
- `k8s/base/configmap.yaml`
- `k8s/overlays/*/kustomization.yaml`

**Current LLM Configuration:**
```yaml
DEFAULT_LLM_PROVIDER: "openai"
LLM_TIMEOUT: "300"
LLM_MAX_RETRIES: "3"
LLM_TEMPERATURE: "0.7"
TESTGEN_LLM_TIMEOUT: "300"
```

**Optional Additions (Not Required):**
If you want to override the new defaults, you can add:
```yaml
# Optional: Override max_tokens for LLM responses (default: 4096)
LLM_MAX_TOKENS: "4096"

# Optional: Override max prompt tokens for testgen (default: 16000)
TESTGEN_MAX_PROMPT_TOKENS: "16000"
```

**Note:** The codegen and critique agents don't support environment variable overrides for MAX_PROMPT_TOKENS - they are hardcoded to 16000 in the Python code.

### ✅ Helm Chart
**Status:** No changes required
**File Reviewed:** `helm/codefactory/values.yaml`

**Current LLM Configuration (lines 209-214):**
```yaml
env:
  DEFAULT_LLM_PROVIDER: "openai"
  LLM_TIMEOUT: "300"
  LLM_MAX_RETRIES: "3"
  LLM_TEMPERATURE: "0.7"
  TESTGEN_LLM_TIMEOUT: "300"
```

**Optional Additions (Not Required):**
If you want to explicitly document the new defaults, you can add to the `env:` section:
```yaml
  # LLM Response Configuration (defaults already set in code)
  # LLM_MAX_TOKENS: "4096"  # Max tokens in LLM response (default: 4096, was 500)
  # TESTGEN_MAX_PROMPT_TOKENS: "16000"  # Max prompt tokens for testgen (default: 16000, was 8192)
```

### ✅ Makefile
**Status:** No changes required
- Test commands use proper test environment setup
- No hardcoded token limits
- Linting and formatting rules unaffected by changes

### ✅ .env.example
**Status:** Optional documentation update recommended
**File:** `.env.example`

**Current Section (lines 460):**
```bash
TESTGEN_LLM_TIMEOUT=300  # Timeout in seconds for LLM calls (default: 300)
```

**Recommended Addition (Optional):**
Add below the TESTGEN_LLM_TIMEOUT line:
```bash
# TESTGEN_MAX_PROMPT_TOKENS=16000  # Max prompt tokens for test generation (default: 16000, increased from 8192)
# LLM_MAX_TOKENS=4096  # Max tokens in LLM response (default: 4096, increased from 500)
```

## Resource Requirements

### Memory Impact
The increased token limits (16000 vs 8192) may result in slightly higher memory usage during prompt processing:
- **Estimated Impact:** +50-100MB per agent process at peak
- **Current Limits:** 
  - Helm: 4Gi limit, 1Gi request
  - K8s: Similar
- **Recommendation:** Current resource limits are adequate. Monitor memory usage in production.

### No CPU Impact
- Token counting is lightweight
- Import fixing adds minimal CPU overhead
- No changes needed to CPU resource limits

## Testing Validation

All fixes validated with comprehensive tests:
```bash
# Run the validation test suite
pytest tests/test_fix_validation.py -v

# Tests validate:
# 1. MAX_PROMPT_TOKENS = 16000 in codegen and critique agents
# 2. tiktoken uses encoding_for_model with fallback
# 3. max_tokens = 4096 in arbiter config
# 4. BaseHTTPMiddleware import instructions present
# 5. Import fixer correctly rewrites fastapi.middleware.base imports
```

## Rollout Recommendations

1. **No Immediate Action Required:** All changes are backward compatible
2. **Monitor Metrics:** Watch for changes in:
   - Prompt token counts (should see fewer truncation warnings)
   - LLM response lengths (should see fewer incomplete responses)
   - Import fixer success rates (should see BaseHTTPMiddleware fixes applied)
3. **Optional:** Add environment variables to documentation for transparency

## Questions or Issues?

If you observe any of the following after deployment:
- Increased memory pressure → Adjust resource limits
- LLM timeout errors → May need to increase `LLM_TIMEOUT` beyond 300s
- Import errors related to middleware → Check import fixer logs

Contact: See repository maintainers

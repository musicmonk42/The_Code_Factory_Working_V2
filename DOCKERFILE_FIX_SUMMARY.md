# Dockerfile Generation Fix - Summary

## Problem
The pipeline was not producing Dockerfiles when code generation was run.

## Root Cause
In `server/services/omnicore_service.py` at line 2034, the pipeline had:

```python
include_deployment = payload.get("include_deployment", False)  # ← Defaulted to False
```

This meant that if the `include_deployment` parameter was not explicitly present in the payload, deployment generation would be SKIPPED.

## Why This Happened
1. The parameter flows through multiple layers: API → Generator Service → OmniCore Service
2. If any layer transformation lost or didn't include the parameter, it defaulted to `False`
3. Even though the API layer set `include_deployment=True`, if it wasn't preserved through all transformations, deployment was skipped

## The Fix
Changed the default value from `False` to `True` in `server/services/omnicore_service.py`:

```python
# FIX: Default to True since deployment is a core pipeline feature
# Users who don't want deployment should explicitly set include_deployment=False
include_deployment = payload.get("include_deployment", True)  # ← Now defaults to True
```

## Impact

### Before Fix
- ❌ Dockerfiles not generated in pipeline
- ❌ Users confused why deployment configs missing
- ❌ Required explicit `include_deployment=True` everywhere

### After Fix
- ✅ Dockerfiles ARE generated in pipeline
- ✅ Deployment is included by default
- ✅ Users can opt-out with `include_deployment=False`

## Backward Compatibility
- ✅ Code that sets `include_deployment=True` → Still works
- ✅ Code that sets `include_deployment=False` → Still works  
- ✅ Code that doesn't set the parameter → Now gets deployment (better default)

## Additional Improvements
Added comprehensive logging throughout the pipeline:

1. **Pipeline Level** (`omnicore_service.py`):
   - Logs `include_deployment` value
   - Shows payload keys for debugging
   - Logs when deployment starts/completes/skips

2. **Deploy Method** (`omnicore_service.py`):
   - Logs full payload when deployment starts
   - Shows deploy agent availability
   - Logs generated configs and files

3. **Deploy Agent** (`deploy_agent/deploy_agent.py`):
   - Shows available plugins
   - Logs plugin lookup
   - Shows config generation details

## Files Modified
1. `server/services/omnicore_service.py`:
   - Line 2034: Changed default `False` → `True`
   - Added debug logging for deployment execution

2. `generator/agents/deploy_agent/deploy_agent.py`:
   - Added plugin discovery logging
   - Added config generation logging

## How to Test
1. Trigger a code generation pipeline
2. Check logs for: `[PIPELINE] Job {id} deployment check: include_deployment=True`
3. Check logs for: `[PIPELINE] Job {id} completed step: deploy`
4. Verify `Dockerfile` appears in `uploads/{job_id}/generated/deploy/` directory

## How to Disable (if needed)
If a user wants to skip deployment generation:

```python
# In API call or pipeline request:
result = await generator_service.run_full_pipeline(
    job_id=job_id,
    readme_content=readme,
    language=language,
    include_deployment=False,  # ← Explicitly disable
)
```

## Related Issues
This fix resolves:
- Pipeline not producing Dockerfiles
- Deployment configs missing from output
- Users having to manually call deploy endpoint separately

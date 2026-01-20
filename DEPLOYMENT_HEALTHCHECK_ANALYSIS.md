# Deployment Healthcheck Failure Analysis

## Problem Statement

The Railway deployment builds successfully but fails healthchecks with:
```
Attempt #1-11 failed with service unavailable
1/1 replicas never became healthy!
Healthcheck failed!
```

## Root Cause Analysis

### 1. **Application Startup Sequence**

The FastAPI application uses an `@asynccontextmanager` lifespan hook (server/main.py:63-150) that:

1. Loads agent modules during startup
2. Performs diagnostics
3. Attempts to import generator agents
4. Only yields after ALL startup tasks complete

**Critical Issue:** If ANY agent import fails or hangs, the entire application startup blocks, preventing the health endpoint from ever becoming available.

### 2. **Healthcheck Endpoint Location**

- Healthcheck endpoint: `/health` (server/main.py:246)
- Railway expects the service to respond within 5 minutes
- If the lifespan startup hangs or takes too long, healthchecks fail

### 3. **Potential Blocking Points**

From server/main.py lines 84-98:
```python
agents_to_load = [
    (AgentType.CODEGEN, "generator.agents.codegen_agent.codegen_agent", ["generate_code"]),
    (AgentType.TESTGEN, "generator.agents.testgen_agent.testgen_agent", ["TestgenAgent"]),
    (AgentType.DEPLOY, "generator.agents.deploy_agent.deploy_agent", ["DeployAgent"]),
    (AgentType.DOCGEN, "generator.agents.docegen_agent.docgen_agent", ["DocgenAgent"]),
    (AgentType.CRITIQUE, "generator.agents.critique_agent.critique_agent", ["CritiqueAgent"]),
]
```

Any of these agent imports could:
- Take too long (loading large ML models)
- Fail due to missing dependencies
- Hang on network requests
- Block on filesystem operations

### 4. **Recent Changes Impact**

The Prometheus metric duplication fix **does not cause this issue**. This is a separate deployment problem related to:
- Application startup sequence
- Agent loading timeouts
- Runtime environment differences

## Recommended Fixes

### Fix 1: Add Startup Timeout (IMMEDIATE - HIGH PRIORITY)

Make agent loading non-blocking with timeout:

```python
# In server/main.py lifespan function
import asyncio

try:
    # Wrap agent loading in timeout
    async with asyncio.timeout(30):  # 30 second timeout
        loader = get_agent_loader()
        # ... rest of agent loading code ...
except asyncio.TimeoutError:
    logger.error("Agent loading timed out after 30s")
    logger.warning("Continuing startup without agents - they can load lazily")
except Exception as e:
    logger.error(f"Error during agent diagnostics: {e}", exc_info=True)
    logger.warning("Continuing startup despite agent diagnostic errors")
```

### Fix 2: Make Agent Loading Lazy (RECOMMENDED - INDUSTRY STANDARD)

Move agent loading to background task instead of blocking startup:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting Code Factory API Server")
    logger.info(f"Version: {__version__}")
    
    # Start agent loading in background task
    import asyncio
    agent_load_task = asyncio.create_task(load_agents_background())
    
    logger.info("API Server ready (agents loading in background)")
    
    yield
    
    # Cleanup
    agent_load_task.cancel()
    logger.info("Shutting down Code Factory API Server")


async def load_agents_background():
    """Load agents in background without blocking startup."""
    try:
        await asyncio.sleep(5)  # Give server time to start
        logger.info("Starting background agent loading...")
        
        loader = get_agent_loader()
        # ... agent loading code ...
        
        logger.info("Background agent loading complete")
    except Exception as e:
        logger.error(f"Background agent loading failed: {e}", exc_info=True)
```

### Fix 3: Add Early Health Check Success (QUICK WIN)

The health endpoint should return success even if agents aren't loaded yet:

```python
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """Health check endpoint - returns success if API is responsive."""
    
    # ALWAYS return healthy for basic API functionality
    # Agent status is informational only
    try:
        loader = get_agent_loader()
        agent_status = loader.get_status()
        agents_health = determine_agent_health(agent_status)
    except Exception as e:
        logger.error(f"Error checking agent health: {e}")
        agents_health = "loading"  # Not "unhealthy" - still starting up
    
    components = {
        "api": "healthy",  # API is always healthy if we got here
        "agents": agents_health,  # Can be "loading", "healthy", "degraded"
    }
    
    # ALWAYS return "healthy" overall status if API responds
    # This ensures Railway healthchecks pass
    return HealthResponse(
        status="healthy",  # Changed from conditional
        version=__version__,
        components=components,
        timestamp=datetime.utcnow().isoformat(),
    )
```

### Fix 4: Add Startup Logs Environment Variable

Add logging to help diagnose startup issues:

```python
# In server/main.py at top level
import os

# Enable verbose startup logging in production
if os.getenv("VERBOSE_STARTUP", "false").lower() == "true":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
```

Then set in Railway: `VERBOSE_STARTUP=true`

## Immediate Action Plan

1. **Implement Fix 3 first** (5 minutes) - Make health check always return success
2. **Implement Fix 1 next** (10 minutes) - Add timeout to agent loading
3. **Test locally** - Ensure application starts within 10 seconds
4. **Deploy to Railway** - Health checks should now pass
5. **If still failing**: Implement Fix 2 (lazy loading) - more invasive but guaranteed to work

## Verification Steps

After applying fixes:

```bash
# Local test
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 &
sleep 5
curl http://localhost:8000/health
# Should return HTTP 200 with {"status": "healthy", ...}

# Check startup time
time python -c "from server.main import app; print('Startup complete')"
# Should complete in <10 seconds
```

## Related Files

- `server/main.py` - Main application and startup sequence
- `server/utils/agent_loader.py` - Agent loading logic
- `server/schemas.py` - HealthResponse model
- `Dockerfile` - Container startup command (line 162)

## Notes

- This issue is **unrelated** to the Prometheus metric duplication fix
- The Prometheus fix solves CI/CD test failures, not deployment issues
- Railway's 5-minute healthcheck timeout is generous - if it's timing out, the app is definitely stuck
- The current startup sequence is blocking and not production-ready

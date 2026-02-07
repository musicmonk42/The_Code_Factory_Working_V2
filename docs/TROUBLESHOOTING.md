<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with the Code Factory platform.

## Table of Contents

- [CI/CD and Build Issues](#cicd-and-build-issues)
- [Dependency Installation Problems](#dependency-installation-problems)
- [Test Collection Timeouts](#test-collection-timeouts)
- [Runtime Errors](#runtime-errors)
- [Performance Issues](#performance-issues)
- [Database and Redis Connection Issues](#database-and-redis-connection-issues)
- [Docker and Container Issues](#docker-and-container-issues)

---

## CI/CD and Build Issues

### Test Collection Timeout

**Symptom**: Test collection hangs or times out during CI runs (e.g., "Test collection timed out after 180 seconds")

**Common Causes**:
1. Heavy imports in conftest.py or test modules
2. Missing system dependencies causing import failures
3. Expensive module-level initialization
4. Network requests during import

**Solutions**:

1. **Check for missing system dependencies**:
   ```bash
   # Ensure these are installed before Python dependencies
   sudo apt-get update
   sudo apt-get install -y libvirt-dev pkg-config
   ```

2. **Review import statements** in test files and conftest.py:
   - Avoid expensive computations at module level
   - Use lazy imports where possible
   - Set environment variables to skip heavy initialization:
     ```bash
     export PYTEST_COLLECTING=1
     export SKIP_AUDIT_INIT=1
     export SKIP_BACKGROUND_TASKS=1
     export NO_MONITORING=1
     export DISABLE_TELEMETRY=1
     ```

3. **Test collection locally with verbose output**:
   ```bash
   timeout 60s pytest --collect-only -v
   ```

### Dependency Installation Failures

**Symptom**: pip or Poetry fails to install dependencies during CI

**Common Causes**:
1. Missing system packages (libvirt-dev, pkg-config, etc.)
2. Poetry cache corruption
3. Conflicting dependency versions
4. Network timeouts

**Solutions**:

1. **Install system dependencies first**:
   ```bash
   sudo apt-get update
   sudo apt-get install -y libvirt-dev pkg-config redis-tools
   ```

2. **Use Poetry with fallback to pip**:
   ```bash
   # Try Poetry first
   poetry install --no-cache --no-root || {
     # Fallback to pip if Poetry fails
     pip install -r requirements.txt -c .github/constraints.txt
   }
   ```

3. **Check for dependency conflicts**:
   ```bash
   pip check
   pip list --format=columns
   ```

4. **Clear Poetry cache** (if Poetry is hanging):
   ```bash
   poetry cache clear pypi --all
   rm -rf ~/.cache/pypoetry
   ```

### CI Workflow Fails on Specific Jobs

**Symptom**: One or more CI jobs fail while others pass

**Diagnosis Steps**:

1. **Check GitHub Actions logs** for the specific job
2. **Look for common error patterns**:
   - `ModuleNotFoundError`: Missing dependency
   - `ImportError`: System library not found
   - `SIGXCPU`: CPU time limit exceeded
   - `MemoryError`: Out of memory

3. **Common fixes by error type**:

   **ModuleNotFoundError (e.g., `No module named 'arbiter'`)**:
   ```bash
   # Ensure editable packages are installed
   pip install -e ./self_fixing_engineer
   pip install -e ./omnicore_engine
   
   # Verify installation
   pip show self_fixing_engineer
   python -c "import arbiter; print(arbiter.__file__)"
   ```

   **ImportError (e.g., `libvirt.so.0: cannot open shared object file`)**:
   ```bash
   # Install missing system library
   sudo apt-get install -y libvirt-dev
   
   # If libvirt-python is commented out in requirements.txt, uncomment it
   # See requirements.txt for the current version to uncomment
   ```

   **SIGXCPU (CPU time limit exceeded)**:
   ```bash
   # Limit thread usage in CI environment
   export OPENBLAS_NUM_THREADS=1
   export MKL_NUM_THREADS=1
   export OMP_NUM_THREADS=1
   ```

---

## Dependency Installation Problems

### libvirt-python Installation Issues

**Symptom**: 
```
ERROR: Could not find a version that satisfies the requirement libvirt-python
error: subprocess-exited-with-error
```

**Solution**:

1. **Install system dependencies BEFORE pip install**:
   ```bash
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install -y libvirt-dev pkg-config python3-dev gcc
   
   # macOS
   brew install libvirt pkg-config
   
   # Then install Python package
   pip install libvirt-python
   ```

2. **If you don't need libvirt**, keep it commented out in requirements.txt (default state)

3. **CI Configuration**: All CI workflows now automatically install libvirt-dev and pkg-config before Python dependencies.

### Poetry Cache Corruption

**Symptom**: Poetry hangs, times out, or fails with cryptic errors

**Solution**:

1. **Clear Poetry cache**:
   ```bash
   poetry cache clear pypi --all
   poetry cache clear _default_cache --all
   rm -rf ~/.cache/pypoetry
   ```

2. **Reinstall dependencies**:
   ```bash
   poetry install --no-cache
   ```

3. **Use pip as fallback** (recommended in CI):
   ```bash
   poetry install --no-cache || pip install -r requirements.txt
   ```

### Protobuf Version Conflicts

**Symptom**: 
```
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed.
protobuf 6.x.x is installed but protobuf<6 is required
```

**Solution**:

1. **Use the constraints file** (already configured in CI):
   ```bash
   pip install -r requirements.txt -c .github/constraints.txt
   ```

2. **Force reinstall with correct version** (using constraints file):
   ```bash
   pip install --force-reinstall -c .github/constraints.txt protobuf
   ```

### Self-Fixing Engineer (SFE) Package Not Found

**Symptom**: 
```
ModuleNotFoundError: No module named 'arbiter'
ImportError: cannot import name 'Arbiter' from 'arbiter'
```

**Solution**:

1. **Install in editable mode**:
   ```bash
   pip install -e ./self_fixing_engineer
   ```

2. **Verify installation**:
   ```bash
   pip show self_fixing_engineer
   python -c "import arbiter; print('Success:', arbiter.__file__)"
   ```

3. **Check PYTHONPATH**:
   ```bash
   export PYTHONPATH="$PYTHONPATH:$PWD"
   ```

---

## Test Collection Timeouts

### Pytest Collection Takes Too Long

**Symptom**: Collection phase takes >60 seconds or times out

**Common Causes**:
- Heavy imports (ML libraries, spaCy models)
- Database connections during import
- Expensive module-level fixtures
- Network requests at import time

**Solutions**:

1. **Enable lazy loading** for heavy dependencies:
   ```bash
   export LAZY_LOAD_ML=1
   export SKIP_IMPORT_TIME_VALIDATION=1
   ```

2. **Use session-scoped fixtures** instead of module-level:
   ```python
   @pytest.fixture(scope="session")
   def expensive_resource():
       # This runs once per test session, not per module
       return initialize_expensive_resource()
   ```

3. **Defer imports** in test files:
   ```python
   # Bad - imports at module level
   import heavy_library
   
   # Good - imports inside test
   def test_something():
       import heavy_library
       ...
   ```

4. **Test collection with timeout**:
   ```bash
   timeout 60s pytest --collect-only -q
   ```

---

## Runtime Errors

### "RuntimeError: no running event loop"

**Symptom**: Async code fails with "no running event loop"

**Solution**:

1. **Use nest_asyncio** (already included in requirements.txt):
   ```python
   import nest_asyncio
   nest_asyncio.apply()
   ```

2. **Ensure async context**:
   ```python
   import asyncio
   
   async def main():
       # Your async code here
       pass
   
   if __name__ == "__main__":
       asyncio.run(main())
   ```

### Missing Configurations

**Symptom**: 
```
WARNING: Missing configurations: WEB3_PROVIDER_URL, SENTRY_DSN
```

**Solution**:

These are **optional** configurations. Set only if using the feature:

```bash
# Blockchain logging (optional)
export WEB3_PROVIDER_URL="https://mainnet.infura.io/v3/YOUR-PROJECT-ID"
export ENABLE_BLOCKCHAIN_LOGGING=true

# Error tracking (recommended for production)
export SENTRY_DSN="https://your-sentry-dsn"
export SENTRY_ENVIRONMENT="production"

# Feature store (optional)
export ENABLE_FEATURE_STORE=1

# HSM support (optional)
export ENABLE_HSM=1
```

### PolicyEngine Initialization Failed

**Symptom**: 
```
WARNING: PolicyEngine initialization failed
```

**Solution**:

This is expected in some environments. The system will:
1. Use fallback configuration
2. Log a warning
3. Continue with degraded functionality

In production, ensure:
```bash
export PRODUCTION_MODE=1
# Set all required configs in .env
```

---

## Performance Issues

### Slow Test Execution

**Solutions**:

1. **Use pytest-xdist for parallel execution**:
   ```bash
   pytest -n auto  # Use all CPU cores
   pytest -n 4     # Use 4 cores
   ```

2. **Limit thread usage** for numpy/ML libraries:
   ```bash
   export OPENBLAS_NUM_THREADS=1
   export MKL_NUM_THREADS=1
   export OMP_NUM_THREADS=1
   ```

3. **Skip slow tests** during development:
   ```bash
   pytest -m "not slow"
   ```

### High Memory Usage

**Solutions**:

1. **Enable lazy loading**:
   ```bash
   export LAZY_LOAD_ML=1
   ```

2. **Use memory profiling**:
   ```bash
   pytest --memray
   ```

3. **Clear caches between test runs**:
   ```bash
   rm -rf .pytest_cache/
   python -c "import gc; gc.collect()"
   ```

---

## Database and Redis Connection Issues

### Redis Connection Refused

**Symptom**: 
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**Solutions**:

1. **Check Redis is running**:
   ```bash
   redis-cli ping  # Should return "PONG"
   ```

2. **Start Redis locally**:
   ```bash
   # Ubuntu/Debian
   sudo systemctl start redis
   
   # macOS
   brew services start redis
   
   # Docker
   docker run -d -p 6379:6379 redis:7-alpine
   ```

3. **Configure Redis URL**:
   ```bash
   export REDIS_URL="redis://localhost:6379"
   ```

4. **Wait for Redis in CI**:
   ```bash
   for i in $(seq 1 30); do
     redis-cli -h localhost ping && break
     echo "Waiting for Redis... $i/30"
     sleep 1
   done
   ```

### Database Migration Issues

**Symptom**: Alembic migration fails

**Solutions**:

1. **Check database connection**:
   ```bash
   export DATABASE_URL="postgresql://user:pass@localhost:5432/dbname"
   python -c "from sqlalchemy import create_engine; engine = create_engine('$DATABASE_URL'); engine.connect()"
   ```

2. **Run migrations**:
   ```bash
   alembic upgrade head
   ```

3. **Reset database** (development only):
   ```bash
   alembic downgrade base
   alembic upgrade head
   ```

---

## Docker and Container Issues

### Docker Build Fails

**Common Issues and Solutions**:

1. **Out of disk space**:
   ```bash
   # Clean up Docker
   docker system prune -af
   docker builder prune -af
   
   # Check disk space
   df -h
   ```

2. **Missing system dependencies in Dockerfile**:
   ```dockerfile
   # Add before pip install
   RUN apt-get update && apt-get install -y \
       libvirt-dev \
       pkg-config \
       && rm -rf /var/lib/apt/lists/*
   ```

3. **Multi-stage build optimization**:
   ```dockerfile
   # Stage 1: Builder
   FROM python:3.11 as builder
   RUN apt-get update && apt-get install -y libvirt-dev pkg-config
   COPY requirements.txt .
   RUN pip install --user -r requirements.txt
   
   # Stage 2: Runtime
   FROM python:3.11-slim
   COPY --from=builder /root/.local /root/.local
   ENV PATH=/root/.local/bin:$PATH
   ```

### Container Startup Issues

**Symptom**: Container exits immediately or crashes on startup

**Diagnosis**:
```bash
# Check logs
docker logs <container-id>

# Run interactively
docker run -it <image> /bin/bash

# Check health
docker inspect <container-id> | jq '.[0].State.Health'
```

**Common Fixes**:

1. **Missing environment variables**:
   ```bash
   docker run -e REDIS_URL=redis://host:6379 ...
   ```

2. **Port conflicts**:
   ```bash
   docker ps  # Check which ports are in use
   docker run -p 8001:8000 ...  # Use different host port
   ```

---

## Getting Help

If you're still experiencing issues:

1. **Check the logs** for specific error messages
2. **Review configuration** in `.env.example` and `.env.production.template`
3. **Search existing issues** in the repository
4. **Open a new issue** with:
   - Error message and full stack trace
   - Steps to reproduce
   - Environment details (OS, Python version, Docker version)
   - Relevant configuration (sanitize secrets!)

## Additional Resources

- [DEPENDENCY_GUIDE.md](./DEPENDENCY_GUIDE.md) - Dependency management details
- [CI_CD_GUIDE.md](./CI_CD_GUIDE.md) - CI/CD pipeline documentation
- [DEPLOYMENT.md](./DEPLOYMENT.md) - Production deployment guide
- [QUICKSTART.md](./QUICKSTART.md) - Quick setup guide
